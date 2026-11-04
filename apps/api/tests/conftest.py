from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.identity import ROLE_PERMISSIONS, UserContext
from app.main import create_app

EXPECTED = json.loads((REPO_ROOT / "data" / "synthetic" / "expected_values.json").read_text())


def user(role: str, uid: str | None = None) -> UserContext:
    return UserContext(uid or f"test.{role}", role, [role], True, frozenset(ROLE_PERMISSIONS[role]))


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        artifact_dir=tmp_path / "artifacts",
        cors_origins="",
        rate_limit_per_minute=1000,
        _env_file=None,
    )
    base.update(overrides)
    return Settings(**base)


class Clients:
    def __init__(self, app):
        self.app = app
        self._clients: dict[str, TestClient] = {}

    def as_user(self, user_id: str) -> TestClient:
        if user_id not in self._clients:
            c = TestClient(self.app)
            r = c.post("/api/v1/auth/mock-login", json={"user_id": user_id})
            assert r.status_code == 200, r.text
            self._clients[user_id] = c
        return self._clients[user_id]

    def anonymous(self) -> TestClient:
        return TestClient(self.app)


@pytest.fixture
def make_app(tmp_path):
    started = []

    def _make(**overrides):
        app = create_app(make_settings(tmp_path, **overrides))
        ctx = TestClient(app)
        ctx.__enter__()  # run lifespan (DB init + seeding)
        started.append(ctx)
        return app, Clients(app)

    yield _make
    for ctx in started:
        ctx.__exit__(None, None, None)


@pytest.fixture
def clients(make_app):
    return make_app()[1]


# --- fake Tableau Server Client (unit tests only; never counts as a real publication) -----
class FakeTableau:
    """Mimics the subset of tableauserverclient used by RestTableauPublisher."""

    def __init__(self, *, project_id="proj-poc", fail_publish=False, fail_signin=False, wrong_project=False, drop_views=False, url=True):
        self.project_id = project_id
        self.fail_publish = fail_publish
        self.fail_signin = fail_signin
        self.wrong_project = wrong_project
        self.drop_views = drop_views
        self.url = url
        self.published: dict[str, dict] = {}
        self.signins = 0
        fake = self

        class ServerResponseError(Exception):
            def __init__(self, code, summary):
                super().__init__(summary)
                self.code, self.summary = code, summary

        self.ServerResponseError = ServerResponseError

        class Server:
            PublishMode = SimpleNamespace(Overwrite="Overwrite")

            def __init__(self, url, use_server_version=True, http_options=None):
                self.auth = SimpleNamespace(sign_in=fake._sign_in, sign_out=lambda: None)
                self.projects = SimpleNamespace(all=lambda: [SimpleNamespace(id=fake.project_id, name="PoC Project"), SimpleNamespace(id="other", name="Other")])
                self.server_info = SimpleNamespace(get=lambda: SimpleNamespace(product_version="2025.1", rest_api_version="3.25"))
                self.workbooks = SimpleNamespace(publish=fake._publish, get_by_id=fake._get, populate_views=fake._populate_views)
                self.views = SimpleNamespace(populate_csv=fake._populate_csv)

        self.Server = Server
        self.Pager = lambda endpoint: endpoint.all()
        self.PersonalAccessTokenAuth = lambda name, secret, site_id="": SimpleNamespace(name=name, site_id=site_id)
        self.WorkbookItem = lambda project_id, name, show_tabs=True: SimpleNamespace(project_id=project_id, name=name)

    def _sign_in(self, auth):
        self.signins += 1
        if self.fail_signin:
            raise self.ServerResponseError("401001", "Signin Error")

    def _publish(self, item, path, mode):
        if self.fail_publish:
            raise self.ServerResponseError("400011", "Bad Request")
        content = Path(path).read_bytes()
        wid = f"wb-{len(self.published) + 1}"
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            twb = next(n for n in z.namelist() if n.endswith(".twb"))
            root = ET.fromstring(z.read(twb))
            csv_text = z.read("Data/results/results.csv").decode()
        # Like real Tableau, hidden worksheets are not published as views.
        hidden = {w.get("name") for w in root.iter("window") if w.get("class") == "worksheet" and w.get("hidden") == "true"}
        sheets = {}
        for ws in root.iter("worksheet"):
            if ws.get("name") in hidden:
                continue
            gf = ws.find(".//groupfilter")
            sheets[ws.get("name")] = gf.get("member").strip('"')
        dash = [d.get("name") for d in root.iter("dashboard")]
        self.published[wid] = {"name": item.name, "project_id": self.project_id if not self.wrong_project else "other", "sheets": sheets, "dash": dash, "csv": csv_text}
        return SimpleNamespace(id=wid, name=item.name, project_id=item.project_id, webpage_url=None)

    def _get(self, wid):
        p = self.published[wid]
        return SimpleNamespace(id=wid, name=p["name"], project_id=p["project_id"], views=[],
                               webpage_url=f"https://tableau.example.test/#/site/poc/workbooks/{wid[3:]}" if self.url else None)

    def _populate_views(self, wb):
        p = self.published[wb.id]
        names = list(p["sheets"]) + p["dash"]
        if self.drop_views:
            names = names[:1]
        wb.views = [SimpleNamespace(id=f"v{i}", name=n, content_url="wb/sheets/" + re.sub(r"\W", "", n), _wid=wb.id) for i, n in enumerate(names)]

    def _populate_csv(self, view):
        import csv as _csv

        p = self.published[view._wid]
        viz = p["sheets"].get(view.name)
        rows = [r for r in _csv.DictReader(io.StringIO(p["csv"])) if r["viz_id"] == viz]
        view.csv = [("Value Label\n" + "\n".join(f'"{r["value_label"]}"' for r in rows)).encode()]
