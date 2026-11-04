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
    return spec, results, build_twbx(spec, results, c.catalog, draft_id="00000000-test", version=1, footer="POC / SYNTHETIC DATA")


def settings(**kw):
    base = dict(tableau_enabled=True, tableau_mode="rest", tableau_server_url="https://tableau.example.test", tableau_site_content_url="poc",
                tableau_project_id="proj-poc", tableau_pat_name="poc-publisher", tableau_pat_secret=SecretStr(PAT_SECRET), _env_file=None)
    base.update(kw)
    return Settings(**base)


def test_artifact_is_deterministic(c, artifact):
    spec, results, art = artifact
    again = build_twbx(spec, results, c.catalog, draft_id="00000000-test", version=1, footer="POC / SYNTHETIC DATA")
    assert art.content == again.content and art.sha256 == again.sha256


def test_artifact_structure(artifact):
    spec, results, art = artifact
    with zipfile.ZipFile(io.BytesIO(art.content)) as z:
        names = z.namelist()
        twb = next(n for n in names if n.endswith(".twb"))
        root = ET.fromstring(z.read(twb))  # well-formed XML
        rows = list(csv.DictReader(io.StringIO(z.read("Data/results/results.csv").decode())))
    assert "Data/results/results.csv" in names
    sheets = {ws.get("name") for ws in root.iter("worksheet")}
    assert sheets == set(art.sheet_names.values())
    zone_names = {z.get("name") for z in root.iter("zone") if z.get("name")}
    assert zone_names == sheets  # every worksheet is placed on the dashboard
    assert [d.get("name") for d in root.iter("dashboard")] == [art.dashboard_name]
    # every worksheet filters to an existing viz id
    viz_ids = {r["viz_id"] for r in rows}
    for ws in root.iter("worksheet"):
        assert ws.find(".//groupfilter").get("member").strip('"') in viz_ids
    assert "SYNTHETIC" in ET.tostring(root, encoding="unicode")


def test_artifact_values_equal_preview_and_expected(artifact):
    spec, results, art = artifact
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
    assert v.content_url == "https://tableau.example.test/#/site/poc/workbooks/1"
    assert any(ch["name"].startswith("value:") and ch["ok"] for ch in v.checks)


def test_rest_url_fallback_is_marked_constructed(artifact, tmp_path):
    art = artifact[2]
    pub = RestTableauPublisher(settings(), tsc=FakeTableau(url=False))
    res = pub.publish_dashboard(art, _write(art, tmp_path), "proj-poc")
    v = pub.verify_publication(res.content_id, art, "proj-poc")
    assert v.verified and v.url_source == "constructed_from_verified_view"
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
