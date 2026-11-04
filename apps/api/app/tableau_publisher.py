"""Tableau publisher adapters.

- RestTableauPublisher: real Tableau REST API through Tableau Server Client (TSC),
  personal-access-token sign-in, publish, then independent read-back verification.
- MockTableauPublisher: development only. It never reports "Published" and returns no URL.
- NotConfiguredPublisher: the safe default; every call explains what is missing.

Credentials stay server-side and never appear in return values, logs or errors.
"""

from __future__ import annotations

import csv
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import Forbidden, IntegrationError
from .tableau_artifact import WorkbookArtifact

log = logging.getLogger("pbac.tableau")


@dataclass
class PublishResult:
    content_id: str
    workbook_name: str
    project_id: str
    webpage_url: str | None
    mode: str


@dataclass
class VerificationResult:
    verified: bool
    content_id: str
    content_url: str | None
    url_source: str = ""
    project_id: str = ""
    project_name: str = ""
    views: list[dict[str, str]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


class TableauPublisher(ABC):
    mode = "abstract"

    @abstractmethod
    def test_connection(self) -> dict[str, Any]: ...

    @abstractmethod
    def list_projects(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def publish_dashboard(self, artifact: WorkbookArtifact, artifact_path: Path, target_project_id: str) -> PublishResult: ...

    @abstractmethod
    def verify_publication(self, content_id: str, artifact: WorkbookArtifact, target_project_id: str) -> VerificationResult: ...

    @abstractmethod
    def get_content_url(self, content_id: str) -> str | None: ...


class NotConfiguredPublisher(TableauPublisher):
    mode = "not_configured"

    def _raise(self):
        raise IntegrationError(
            "Tableau integration not configured. An administrator must set TABLEAU_ENABLED, TABLEAU_SERVER_URL, "
            "TABLEAU_SITE_CONTENT_URL, TABLEAU_PROJECT_ID and a publisher personal access token.",
            code="TABLEAU_NOT_CONFIGURED",
            status_code=503,
        )

    def test_connection(self):
        return {"ok": False, "mode": self.mode, "message": "Tableau integration not configured."}

    def list_projects(self):
        self._raise()

    def publish_dashboard(self, artifact, artifact_path, target_project_id):
        self._raise()

    def verify_publication(self, content_id, artifact, target_project_id):
        self._raise()

    def get_content_url(self, content_id):
        return None


class MockTableauPublisher(TableauPublisher):
    """Development-only stand-in. A mock publish is NOT a Tableau publication."""

    mode = "mock"

    def test_connection(self):
        return {"ok": True, "mode": self.mode, "message": "MOCK mode - no Tableau server was contacted."}

    def list_projects(self):
        return [{"id": "mock-project", "name": "MOCK PoC Project (not real)"}]

    def publish_dashboard(self, artifact, artifact_path, target_project_id):
        return PublishResult(f"mock-{artifact.sha256[:12]}", artifact.name, target_project_id, None, self.mode)

    def verify_publication(self, content_id, artifact, target_project_id):
        return VerificationResult(False, content_id, None, reason="Mock mode cannot verify a real Tableau publication.")

    def get_content_url(self, content_id):
        return None


def _tableau_error(exc: Exception, action: str) -> IntegrationError:
    code = getattr(exc, "code", None)
    summary = getattr(exc, "summary", None) or exc.__class__.__name__
    detail = f" (Tableau error {code}: {summary})" if code else f" ({summary})"
    return IntegrationError(f"Tableau {action} failed{detail}. The draft was preserved; you can retry.", code="TABLEAU_" + action.upper().replace(" ", "_") + "_FAILED")


class RestTableauPublisher(TableauPublisher):
    mode = "rest"

    def __init__(self, settings: Settings, tsc: Any = None):
        if tsc is None:
            import tableauserverclient as tsc  # imported lazily so the app runs without Tableau
        self.tsc = tsc
        self.s = settings

    # --- session helpers ---------------------------------------------------------------
    def _server(self):
        server = self.tsc.Server(self.s.tableau_server_url, use_server_version=True,
                                 http_options={"verify": self.s.tableau_verify_ssl, "timeout": self.s.tableau_timeout_seconds})
        auth = self.tsc.PersonalAccessTokenAuth(self.s.tableau_pat_name, self.s.tableau_pat_secret.get_secret_value(), site_id=self.s.tableau_site_content_url)
        server.auth.sign_in(auth)
        return server

    def _close(self, server) -> None:
        try:
            server.auth.sign_out()
        except Exception as exc:  # noqa: BLE001 - sign-out failure must not mask the result
            log.warning("tableau sign-out failed: %s", exc.__class__.__name__)

    def _projects(self, server) -> list[Any]:
        return list(self.tsc.Pager(server.projects))

    def _site_url(self) -> str:
        base = self.s.tableau_server_url.rstrip("/")
        return f"{base}/#/site/{self.s.tableau_site_content_url}" if self.s.tableau_site_content_url else f"{base}/#"

    # --- contract ----------------------------------------------------------------------
    def test_connection(self) -> dict[str, Any]:
        try:
            server = self._server()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": self.mode, "message": _tableau_error(exc, "sign in").message}
        try:
            info = {}
            try:
                si = server.server_info.get()
                info = {"product_version": getattr(si, "product_version", None), "rest_api_version": getattr(si, "rest_api_version", None)}
            except Exception as exc:  # noqa: BLE001 - server info is optional on some sites
                log.info("tableau server info unavailable: %s", exc.__class__.__name__)
            projects = {p.id: p.name for p in self._projects(server)}
            target = self.s.tableau_project_id
            if not target:
                message = "Connected. Choose a target project: set TABLEAU_PROJECT_ID to one of the listed project IDs."
            elif target in projects:
                message = "Connected; target project validated."
            else:
                message = "Connected, but the configured target project was not found or is not visible to the publisher identity."
            return {
                "ok": target in projects,
                "signed_in": True,
                "mode": self.mode,
                "server_url": self.s.tableau_server_url,
                "site": self.s.tableau_site_content_url or "(default)",
                **info,
                "target_project_id": target or None,
                "target_project_found": target in projects,
                "target_project_name": projects.get(target),
                "project_count": len(projects),
                "message": message,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": self.mode, "message": _tableau_error(exc, "connection test").message}
        finally:
            self._close(server)

    def list_projects(self) -> list[dict[str, str]]:
        server = self._server_or_raise("project listing")
        try:
            allowed = set(self.s.allowed_project_ids)
            return [{"id": p.id, "name": p.name, "allowed": p.id in allowed} for p in self._projects(server)]
        except Exception as exc:  # noqa: BLE001
            raise _tableau_error(exc, "project listing") from exc
        finally:
            self._close(server)

    def _server_or_raise(self, action: str):
        try:
            return self._server()
        except Exception as exc:  # noqa: BLE001
            raise _tableau_error(exc, "sign in") from exc

    def publish_dashboard(self, artifact: WorkbookArtifact, artifact_path: Path, target_project_id: str) -> PublishResult:
        if target_project_id not in self.s.allowed_project_ids:
            raise Forbidden("Target Tableau project is not allow-listed.", code="TABLEAU_PROJECT_NOT_ALLOWED")
        server = self._server_or_raise("publish")
        try:
            if target_project_id not in {p.id for p in self._projects(server)}:
                raise IntegrationError("Target Tableau project was not found for the publisher identity.", code="TABLEAU_PROJECT_NOT_FOUND")
            item = self.tsc.WorkbookItem(project_id=target_project_id, name=artifact.name, show_tabs=True)
            published = server.workbooks.publish(item, str(artifact_path), self.tsc.Server.PublishMode.Overwrite)
            return PublishResult(published.id, published.name, target_project_id, getattr(published, "webpage_url", None), self.mode)
        except (IntegrationError, Forbidden):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _tableau_error(exc, "publish") from exc
        finally:
            self._close(server)

    def verify_publication(self, content_id: str, artifact: WorkbookArtifact, target_project_id: str) -> VerificationResult:
        """Independently read the workbook back from Tableau and check it is the one we published."""
        server = self._server_or_raise("verification")
        checks: list[dict[str, Any]] = []
        try:
            wb = server.workbooks.get_by_id(content_id)
            server.workbooks.populate_views(wb)
            views = [{"id": v.id, "name": v.name, "content_url": getattr(v, "content_url", "")} for v in wb.views]
            checks.append({"name": "workbook_exists", "ok": wb.id == content_id})
            checks.append({"name": "project_matches", "ok": wb.project_id == target_project_id})
            checks.append({"name": "name_matches", "ok": wb.name == artifact.name})
            names = {v["name"] for v in views}
            checks.append({"name": "dashboard_view_present", "ok": artifact.dashboard_name in names})
            checks.append({"name": "all_sheets_present", "ok": set(artifact.sheet_names.values()) <= names})

            # Value check: KPI worksheets must render the same labels as the validated preview.
            kpi_labels = {artifact.sheet_names[r["viz_id"]]: r["value_label"] for r in artifact.csv_rows if r["viz_id"].startswith("kpi_")}
            for v in wb.views:
                if v.name in kpi_labels:
                    try:
                        server.views.populate_csv(v)
                        text = b"".join(v.csv).decode("utf-8", "replace")
                        ok = kpi_labels[v.name] in {c.strip() for row in csv.reader(io.StringIO(text)) for c in row}
                        checks.append({"name": f"value:{v.name}", "ok": ok, "expected": kpi_labels[v.name]})
                    except Exception as exc:  # noqa: BLE001
                        checks.append({"name": f"value:{v.name}", "ok": False, "error": getattr(exc, "summary", exc.__class__.__name__)})

            # Prefer the dashboard view itself (built from the content URL Tableau returned for the
            # verified view); fall back to the workbook page Tableau reports.
            url, source = None, ""
            dash = next((v for v in views if v["name"] == artifact.dashboard_name), None)
            if dash and dash["content_url"]:
                url, source = f"{self._site_url()}/views/{dash['content_url'].replace('/sheets/', '/')}", "tableau_dashboard_view"
            elif getattr(wb, "webpage_url", None):
                url, source = wb.webpage_url, "tableau_webpage_url"
            checks.append({"name": "content_url_present", "ok": bool(url)})
            pname = next((p.name for p in self._projects(server) if p.id == wb.project_id), "")
            verified = all(c["ok"] for c in checks)
            return VerificationResult(verified, content_id, url if verified else None, source, wb.project_id, pname, views, checks,
                                      "" if verified else "One or more verification checks failed.")
        except Exception as exc:  # noqa: BLE001
            err = _tableau_error(exc, "verification")
            return VerificationResult(False, content_id, None, checks=checks, reason=err.message)
        finally:
            self._close(server)

    def get_content_url(self, content_id: str) -> str | None:
        server = self._server_or_raise("lookup")
        try:
            return getattr(server.workbooks.get_by_id(content_id), "webpage_url", None)
        except Exception as exc:  # noqa: BLE001
            raise _tableau_error(exc, "lookup") from exc
        finally:
            self._close(server)


def get_publisher(settings: Settings) -> TableauPublisher:
    if not settings.tableau_credentials_configured:
        return NotConfiguredPublisher()
    if settings.tableau_mode == "mock":
        return MockTableauPublisher()
    return RestTableauPublisher(settings)
