"""Dev tool: publish sample dashboards to the Tableau dev project and save how Tableau renders them.

Design work on the generated workbook is judged on Tableau's own rendering, not on the XML.
This builds the .twbx for a few sample questions, publishes each one (overwriting a fixed
"PoC design preview" workbook in the configured non-production project) and saves the
dashboard image to var/renders/<name>.png.

    cd apps/api && .venv/Scripts/python ../../scripts/render_dashboards.py [--offline] [--only N]

--offline only writes the .twbx files (no Tableau calls). Synthetic data only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))  # app + tests.conftest

from app.insights import key_findings  # noqa: E402
from app.runtime import Container  # noqa: E402
from app.tableau_artifact import build_twbx  # noqa: E402

Q1 = "Show monthly SME credit outstanding by region for 2025."
SAMPLES = [  # (question, role, clarifications, spec overrides) - "auto" recipe/theme unless overridden
    (Q1, "business_user", {}, {}),
    ("Show credit outstanding and NPL by region", "business_user", {}, {}),
    ("Monthly credit outstanding trend per cabang dashboard for 2025", "business_user", {}, {}),
    ("Compare deposit growth across regions", "ba_reviewer", {}, {}),
    (Q1, "business_user", {}, {"recipe": "rail"}),
    (Q1, "business_user", {}, {"recipe": "executive", "theme": "teal"}),
]


def build(c: Container, i: int, question: str, role: str, clar: dict, overrides: dict):
    from tests.conftest import user

    u = user(role)
    ctx = c.semantic_context(u)
    intent = c.planner.classify_intent(question, ctx, clar).intent
    if intent is None:
        raise SystemExit(f"sample {i}: planner asked for clarification: {question}")
    spec = c.planner.generate_dashboard_spec(intent, ctx).model_copy(update=overrides)
    results = c.data.execute_validated_plan(spec, u)
    dims, parts = c.data.components(spec, u)
    art = build_twbx(spec, results, c.catalog, dims=dims, components=parts, draft_id=f"design{i:03d}", version=1,
                     footer="POC / SYNTHETIC DATA | design preview", findings=key_findings(spec, results, c.catalog))
    art.name = f"PoC design preview {i}"
    return art


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", type=int)
    args = ap.parse_args()
    out = ROOT / "var" / "renders"
    out.mkdir(parents=True, exist_ok=True)
    c = Container()
    arts = []
    for i, (q, role, clar, overrides) in enumerate(SAMPLES, 1):
        if args.only and i != args.only:
            continue
        art = build(c, i, q, role, clar, overrides)
        path = out / f"preview_{i}.twbx"
        path.write_bytes(art.content)
        arts.append((i, art, path))
        print(f"built {path.name}: {art.dashboard_name}")
    if args.offline:
        return

    import tableauserverclient as tsc

    s = c.settings
    if not s.tableau_configured:
        raise SystemExit("Tableau is not configured (.env); use --offline.")
    server = tsc.Server(s.tableau_server_url, use_server_version=True, http_options={"verify": s.tableau_verify_ssl, "timeout": 120})
    server.auth.sign_in(tsc.PersonalAccessTokenAuth(s.tableau_pat_name, s.tableau_pat_secret.get_secret_value(),
                                                    site_id=s.tableau_site_content_url))
    try:
        for i, art, path in arts:
            wb = server.workbooks.publish(tsc.WorkbookItem(project_id=s.tableau_project_id, name=art.name, show_tabs=True),
                                          str(path), tsc.Server.PublishMode.Overwrite)
            server.workbooks.populate_views(wb)
            view = next(v for v in wb.views if v.name == art.dashboard_name)
            server.views.populate_image(view, tsc.ImageRequestOptions(imageresolution=tsc.ImageRequestOptions.Resolution.High, maxage=1))
            png = out / f"preview_{i}.png"
            png.write_bytes(view.image)
            print(f"rendered {png}")
    finally:
        server.auth.sign_out()


if __name__ == "__main__":
    main()
