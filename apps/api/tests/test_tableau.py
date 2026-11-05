"""Deterministic workbook generation and Tableau adapter behaviour (fake TSC, unit level).

These tests do NOT prove a real publication. The real-environment check is
tests/e2e/test_tableau_real_e2e.py, which fails when Tableau is mocked.
"""

import csv
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.errors import Forbidden, IntegrationError
from app.planner import RulesPlanner
from app.runtime import Container
from app.tableau_artifact import build_twbx, format_value
from app.tableau_publisher import MockTableauPublisher, NotConfiguredPublisher, RestTableauPublisher, get_publisher
from tests.conftest import EXPECTED, FakeTableau, user

PAT_SECRET = "super-secret-pat-value-123"


@pytest.fixture(scope="module")
def c():
    return Container()


@pytest.fixture(scope="module")
def artifact(c):
    u = user("business_user")
    intent = c.planner.classify_intent("Show monthly SME credit outstanding by region for 2025.", c.semantic_context(u), {}).intent
    spec = c.planner.generate_dashboard_spec(intent, c.semantic_context(u))
    results = c.data.execute_validated_plan(spec, u)
    dims, parts = c.data.components(spec, u)
    art = build_twbx(spec, results, c.catalog, dims=dims, components=parts, draft_id="00000000-test", version=1, footer="POC / SYNTHETIC DATA")
    return spec, results, art, dims, parts


def settings(**kw):
    base = dict(tableau_enabled=True, tableau_mode="rest", tableau_server_url="https://tableau.example.test", tableau_site_content_url="poc",
                tableau_project_id="proj-poc", tableau_pat_name="poc-publisher", tableau_pat_secret=SecretStr(PAT_SECRET), _env_file=None)
    base.update(kw)
    return Settings(**base)


def test_artifact_is_deterministic(c, artifact):
    spec, results, art, dims, parts = artifact
    again = build_twbx(spec, results, c.catalog, dims=dims, components=parts, draft_id="00000000-test", version=1, footer="POC / SYNTHETIC DATA")
    assert art.content == again.content and art.sha256 == again.sha256


def _twb(art):
    with zipfile.ZipFile(io.BytesIO(art.content)) as z:
        names = z.namelist()
        root = ET.fromstring(z.read(next(n for n in names if n.endswith(".twb"))))  # well-formed XML
        rows = list(csv.DictReader(io.StringIO(z.read("Data/results/kpi_parts.csv").decode())))
    return names, root, rows


def test_artifact_structure(artifact):
    spec, results, art, dims, parts = artifact
    names, root, rows = _twb(art)
    assert "Data/results/kpi_parts.csv" in names and len(rows) == len(parts)
    sheets = {ws.get("name") for ws in root.iter("worksheet")}
    assert sheets == set(art.sheet_names.values())
    zone_names = {z.get("name") for z in root.iter("zone") if z.get("name")}
    assert zone_names == sheets  # every worksheet (KPI cards included) is on the dashboard
    assert set(art.kpi_expected) == {art.sheet_names[r.viz_id] for r in results if r.viz_type == "kpi_card"}
    assert not [z for z in root.iter("zone") if z.get("type-v2") == "color"]  # 8 regions over time: a heatmap, no legend
    assert "Square" in {m.get("class") for m in root.iter("mark")}
    assert [d.get("name") for d in root.iter("dashboard")] == [art.dashboard_name]
    assert "SYNTHETIC" in ET.tostring(root, encoding="unicode")


def test_dashboard_filters_are_parameters_applied_to_every_sheet(artifact):
    spec, results, art, dims, parts = artifact
    _, root, _ = _twb(art)
    controls = [z.get("param") for z in root.iter("zone") if z.get("type-v2") == "paramctrl"]
    params = {c.get("caption"): c for c in root.find("datasources").find("datasource[@name='Parameters']").iter("column")}
    assert set(params) == {"Region", "Branch (synthetic code)", "Product segment"} and len(controls) == 3
    assert [m.get("value") for m in params["Region"].iter("member")][:2] == ['"All"', '"Jakarta"']
    for ws in root.iter("worksheet"):
        cols = [f.get("column") for f in ws.iter("filter")]
        assert any(c.endswith("[none:Calculation_ok:nk]") for c in cols), ws.get("name")  # every sheet obeys the filters


def test_kpi_parts_reproduce_the_validated_results(artifact):
    """Tableau applies sum-then-divide to the parts; check that this equals the app's KPI values."""
    spec, results, art, dims, parts = artifact

    def kpi(metric_id, keep=lambda r: True):
        rr = [r for r in parts if r["scope"] == "range" and r["metric_id"] == metric_id and keep(r)]
        a, b = sum(r["a"] for r in rr), sum(r["b"] or 0 for r in rr)
        kind = {"total_outstanding": "sum", "outstanding_growth_pct": "growth", "target_achievement_pct": "ratio"}[metric_id]
        return a if kind == "sum" else (a / b - 1) * 100 if kind == "growth" else a / b * 100

    for r in results:
        if r.viz_type == "kpi_card":
            mid = r.columns[0]["key"]
            assert kpi(mid) == pytest.approx(r.rows[0][mid], rel=1e-9, abs=1e-6)  # app rounds percentages to 6 decimals
    by_region = next(r for r in results if r.viz_id == "comparison")
    for row in by_region.rows:
        assert kpi("total_outstanding", lambda p, reg=row["region"]: p["region"] == reg) == pytest.approx(row["total_outstanding"], rel=1e-9)


def test_worksheets_are_not_hidden(artifact):
    """Hidden sheets are not published as views, which breaks value read-back (found on Tableau Cloud)."""
    with zipfile.ZipFile(io.BytesIO(artifact[2].content)) as z:
        root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".twb"))))
    windows = [w for w in root.iter("window") if w.get("class") == "worksheet"]
    assert len(windows) == len(artifact[2].sheet_names)
    assert not any(w.get("hidden") == "true" for w in windows)


def test_artifact_values_equal_preview_and_expected(artifact):
    spec, results, art, _dims, _parts = artifact
    kpi = next(r for r in art.csv_rows if r["viz_id"] == "kpi_total_outstanding")
    assert float(kpi["value"]) == EXPECTED["demo_sme_credit"]["total_outstanding_2025_12"]
    bars = {r["category"]: float(r["value"]) for r in art.csv_rows if r["viz_id"] == "comparison"}
    assert bars == {k: float(v) for k, v in EXPECTED["demo_sme_credit"]["total_outstanding_by_region_2025_12"].items()}


def test_format_value():
    assert format_value(11_947_952_000_000, "IDR") == "Rp 11.95 T"
    assert format_value(2_153_408_000_000 / 1000, "IDR") == "Rp 2.15 M"
    assert format_value(101.234, "%") == "101.23%"
    assert format_value(None, "IDR") == "-"


def test_factory_defaults_to_not_configured():
    assert isinstance(get_publisher(Settings(_env_file=None)), NotConfiguredPublisher)
    assert isinstance(get_publisher(settings(tableau_pat_secret=SecretStr(""))), NotConfiguredPublisher)
    assert isinstance(get_publisher(settings(tableau_mode="mock")), MockTableauPublisher)


def test_credentials_without_project_can_connect_but_not_publish():
    s = settings(tableau_project_id="")
    assert s.tableau_credentials_configured and not s.tableau_configured
    pub = get_publisher(s)
    assert isinstance(pub, RestTableauPublisher)
    pub.tsc = FakeTableau()
    conn = pub.test_connection()
    assert conn["signed_in"] and not conn["ok"] and conn["project_count"] == 2
    assert "TABLEAU_PROJECT_ID" in conn["message"]
    assert {p["id"] for p in pub.list_projects()} == {"proj-poc", "other"}


def test_not_configured_never_fakes_success(artifact, tmp_path):
    p = NotConfiguredPublisher()
    assert p.test_connection()["ok"] is False
    with pytest.raises(IntegrationError) as e:
        p.publish_dashboard(artifact[2], tmp_path / "x.twbx", "proj")
    assert e.value.code == "TABLEAU_NOT_CONFIGURED"


def test_mock_never_verifies(artifact, tmp_path):
    p = MockTableauPublisher()
    r = p.publish_dashboard(artifact[2], tmp_path / "x.twbx", "mock-project")
    v = p.verify_publication(r.content_id, artifact[2], "mock-project")
    assert r.webpage_url is None and v.verified is False and v.content_url is None


def _write(art, tmp_path):
    path = tmp_path / art.filename
    path.write_bytes(art.content)
    return path


def test_rest_publish_and_verify(artifact, tmp_path):
    art = artifact[2]
    fake = FakeTableau()
    pub = RestTableauPublisher(settings(), tsc=fake)
    conn = pub.test_connection()
    assert conn["ok"] and conn["target_project_found"] and PAT_SECRET not in str(conn)
    res = pub.publish_dashboard(art, _write(art, tmp_path), "proj-poc")
    v = pub.verify_publication(res.content_id, art, "proj-poc")
    assert v.verified, v.checks
    assert v.url_source == "tableau_dashboard_view"
    assert v.content_url.startswith("https://tableau.example.test/#/site/poc/views/wb/")  # opens the dashboard, not the workbook page
    assert any(ch["name"].startswith("value:") and ch["ok"] for ch in v.checks)


def test_rest_url_works_without_webpage_url(artifact, tmp_path):
    art = artifact[2]
    pub = RestTableauPublisher(settings(), tsc=FakeTableau(url=False))
    res = pub.publish_dashboard(art, _write(art, tmp_path), "proj-poc")
    v = pub.verify_publication(res.content_id, art, "proj-poc")
    assert v.verified and v.url_source == "tableau_dashboard_view"
    assert v.content_url.startswith("https://tableau.example.test/#/site/poc/views/")


@pytest.mark.parametrize("kw,check", [({"wrong_project": True}, "project_matches"), ({"drop_views": True}, "dashboard_view_present")])
def test_rest_verification_failures_never_return_url(artifact, tmp_path, kw, check):
    art = artifact[2]
    pub = RestTableauPublisher(settings(), tsc=FakeTableau(**kw))
    res = pub.publish_dashboard(art, _write(art, tmp_path), "proj-poc")
    v = pub.verify_publication(res.content_id, art, "proj-poc")
    assert not v.verified and v.content_url is None
    assert any(ch["name"] == check and not ch["ok"] for ch in v.checks)


def test_rest_errors_are_safe(artifact, tmp_path):
    art = artifact[2]
    pub = RestTableauPublisher(settings(), tsc=FakeTableau(fail_publish=True))
    with pytest.raises(IntegrationError) as e:
        pub.publish_dashboard(art, _write(art, tmp_path), "proj-poc")
    assert "400011" in e.value.message and PAT_SECRET not in e.value.message
    bad = RestTableauPublisher(settings(), tsc=FakeTableau(fail_signin=True))
    conn = bad.test_connection()
    assert conn["ok"] is False and PAT_SECRET not in str(conn)


def test_rest_rejects_non_allowlisted_project(artifact, tmp_path):
    pub = RestTableauPublisher(settings(), tsc=FakeTableau())
    with pytest.raises(Forbidden):
        pub.publish_dashboard(artifact[2], _write(artifact[2], tmp_path), "other")


def test_rules_planner_has_no_network_dependency():
    assert RulesPlanner.name == "mock-rules"


# --- design system (tableau_theme.py) -------------------------------------------------------


def test_style_choice_follows_content_shape_and_overrides():
    from app.tableau_theme import THEMES, choose_style

    assert choose_style("auto", "auto", 0, 1, False, "x")[0] == "executive"
    assert choose_style("auto", "auto", 2, 3, True, "x")[0] in ("rail", "executive")
    assert choose_style("auto", "auto", 5, 3, True, "x")[0] in ("band", "executive")
    recipe, theme = choose_style("rail", "slate", 0, 1)
    assert (recipe, theme.name) == ("rail", "slate")  # explicit choices win
    assert choose_style("auto", "auto", 3, 3, True, "same") == choose_style("auto", "auto", 3, 3, True, "same")  # stable
    picked = {choose_style("auto", "auto", 3, 3, True, f"dashboard {i}")[1].name for i in range(40)}
    assert len(picked) >= 4 and picked <= set(THEMES)  # different dashboards get different looks


def test_chart_form_follows_the_kpi_and_shape(c):
    from app.tableau_artifact import chart_form

    m = c.catalog.metric
    assert chart_form("line", m("total_outstanding"), 0, True, 0, False) == "area"
    assert chart_form("line", m("total_outstanding"), 1, True, 4, False) == "spotlight"
    assert chart_form("line", m("total_outstanding"), 1, True, 8, False) == "heatmap"
    assert chart_form("bar", m("target_achievement_pct"), 1, False, 8, False) == "target"
    assert chart_form("bar", m("outstanding_growth_pct"), 1, False, 8, False) == "dots"
    assert chart_form("bar", m("total_outstanding"), 1, False, 8, False) == "ranked"
    assert chart_form("bar", m("total_outstanding"), 0, True, 0, False) == "columns"
    assert chart_form("bar", m("total_outstanding"), 1, False, 8, True) == "highlight"


def test_forms_are_encoded_for_tableau(artifact):
    _, root, _ = _twb(artifact[2])
    cols = {c.get("name"): c for c in root.find("datasources").find("datasource[@caption='PoC KPI parts (Synthetic)']").findall("column")}
    rank = next(c for n, c in cols.items() if "RANK(" in (c.find("calculation").get("formula") if c.find("calculation") is not None else ""))
    assert rank.find("calculation/table-calc").get("ordering-type") == "Columns"  # down the members: top 3 recomputed under every filter
    refs = {r.get("formula"): r for r in root.iter("reference-line")}
    assert refs["constant"].get("value") == "1" and "average" in refs  # 100% target line, growth average
    ramp = next(e for e in root.iter("encoding") if e.get("type") == "custom-interpolated")
    assert len(ramp.findall("color-palette/color")) == 3
    titles = ["".join(r.text or "" for r in ws.iter("run")) for ws in root.iter("worksheet") if ws.find("layout-options") is not None]
    assert any("below the 100% target" in t for t in titles) and any("grew fastest" in t for t in titles)


def _build(c, artifact, **overrides):
    spec, results, _art, dims, parts = artifact
    spec = spec.model_copy(update=overrides)
    return build_twbx(spec, results, c.catalog, dims=dims, components=parts, draft_id="00000000-test", version=1,
                      footer="POC / SYNTHETIC DATA", findings=["A finding."])


def test_every_sheet_is_clean_and_every_zone_is_a_card(artifact):
    _, root, _ = _twb(artifact[2])
    for ws in root.iter("worksheet"):
        rules = {r.get("element"): r for r in ws.find("table/style").findall("style-rule")}
        assert {f.get("scope") for f in rules["gridline"].findall("format") if f.get("value") == "off"} == {"rows", "cols"}, ws.get("name")
        assert rules["zeroline"].find("format").get("value") == "off"
    sheet_zones = [z for z in root.iter("zone") if z.get("name") and z.get("type-v2") is None]
    for z in sheet_zones:
        fmts = {f.get("attr"): f.get("value") for f in z.find("zone-style").findall("format")}
        assert fmts.get("background-color") and int(fmts["padding-left"]) > 0, z.get("name")
    texts = [z for z in root.iter("zone") if z.get("type-v2") == "text"]
    top = {f.get("id") for f in root.iter("format") if f.get("attr") == "vertical-align" and f.get("value") == "top"}
    assert {f"dash-text_{z.get('id')}" for z in texts} <= top


def test_kpi_card_shows_change_vs_previous_period_and_sparkline(artifact):
    spec, results, art, _dims, _parts = artifact
    _, root, _ = _twb(art)
    calcs = {c.get("name"): c.find("calculation").get("formula") for c in root.find("datasources").iter("column")
             if c.find("calculation") is not None}
    ups = [f for n, f in calcs.items() if n.startswith("[Calculation_u")]
    assert ups and all("#2025-12-01#" in f and "#2025-11-01#" in f for f in ups)  # last vs previous month
    kpi = art.sheet_names["kpi_total_outstanding"]
    ws = next(w for w in root.iter("worksheet") if w.get("name") == kpi)
    label = "".join(r.text or "" for r in ws.iter("run"))
    assert "Dec 2025 vs Nov 2025" in label and "Æ\n" in label
    assert "kpi_total_outstanding__trend" in art.sheet_names  # sparkline published as its own (visible) sheet
    growth = next(k for k in art.sheet_names if k.startswith("kpi_") and "growth" in k)
    assert f"{growth}__trend" not in art.sheet_names  # no period-over-period change for a growth rate


@pytest.mark.parametrize("recipe", ["executive", "band", "rail"])
def test_every_recipe_keeps_all_sheets_and_the_synthetic_notice(c, artifact, recipe):
    art = _build(c, artifact, recipe=recipe)
    _, root, _ = _twb(art)
    zone_names = {z.get("name") for z in root.iter("zone") if z.get("name")}
    assert zone_names == set(art.sheet_names.values())
    assert "POC / SYNTHETIC DATA - not actual bank performance" in ET.tostring(root, encoding="unicode")
    size = root.find(".//dashboard/size")
    assert size.get("minheight") == size.get("maxheight") and int(size.get("maxwidth")) == 1600
    for z in root.iter("zone"):
        if z.get("id") != "1":
            assert 0 <= int(z.get("x")) and int(z.get("x")) + int(z.get("w")) <= 100001, z.attrib


def test_preview_design_plan_is_what_the_workbook_uses(c, artifact):
    from app.tableau_artifact import design_plan

    spec, results, art, _dims, _parts = artifact
    plan = design_plan(spec, results, c.catalog)
    d = plan.as_dict()
    _, root, _ = _twb(art)
    titles = {ws.get("name"): "".join(r.text or "" for r in ws.iter("run")) for ws in root.iter("worksheet")}
    for vid, v in d["vizzes"].items():
        if v["form"] != "kpi_card":
            assert v["headline"] in titles[art.sheet_names[vid]]  # same headline in the app preview and in Tableau
    page = root.find(".//dashboard/style/style-rule[@element='table']/format[@attr='background-color']").get("value")
    assert d["theme"]["page"] == page and d["hero"] in art.sheet_names
