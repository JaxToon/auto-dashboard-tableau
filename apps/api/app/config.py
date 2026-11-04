"""Application settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "local"
    app_mode: str = "synthetic"
    app_base_url: str = "http://localhost:3000"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'var' / 'app.db').as_posix()}"
    data_dir: Path = REPO_ROOT / "data"
    artifact_dir: Path = REPO_ROOT / "var" / "artifacts"

    # Mock login is only for isolated local development and is always labelled as mock.
    auth_mode: str = "mock"
    session_ttl_minutes: int = 480

    llm_provider: str = "mock"
    llm_model: str = "mock-dashboard-planner"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")

    # Tableau. TABLEAU_MODE: "rest" (real REST API via TSC) or "mock" (development only, never "Published").
    tableau_enabled: bool = False
    tableau_mode: str = "rest"
    tableau_server_url: str = ""
    tableau_site_content_url: str = ""
    tableau_project_id: str = ""
    tableau_allowed_project_ids: str = ""  # comma separated; defaults to TABLEAU_PROJECT_ID
    tableau_auth_method: str = "pat"
    tableau_pat_name: str = ""
    tableau_pat_secret: SecretStr = SecretStr("")
    tableau_verify_ssl: bool = True
    tableau_timeout_seconds: int = 120

    # Feature flags / kill switches. Env values are ceilings: admins can switch a feature
    # off at runtime but cannot switch on a feature the deployment has disabled.
    feature_ai_planning: bool = True
    feature_data_access: bool = True
    feature_dashboard_publish: bool = False
    feature_recurring_recommendations: bool = True
    feature_recommendation_telemetry: bool = True
    feature_conversational_analytics_integration: bool = False
    review_required: bool = True

    telemetry_actor_salt: SecretStr = SecretStr("")
    seed_simulated_events: bool = True

    # Limits.
    max_prompt_chars: int = 1000
    max_result_rows: int = 5000
    max_series: int = 12
    max_categories: int = 40
    rate_limit_per_minute: int = 30

    audit_log_level: str = "metadata"
    cors_origins: str = "http://localhost:3000"

    # Recommendation defaults (local demo test parameters, not bank policy).
    rec_min_events: int = Field(default=10)
    rec_window_days: int = Field(default=30)
    rec_min_actors: int = Field(default=3)

    @property
    def allowed_project_ids(self) -> list[str]:
        ids = [p.strip() for p in self.tableau_allowed_project_ids.split(",") if p.strip()]
        if not ids and self.tableau_project_id:
            ids = [self.tableau_project_id]
        return ids

    @property
    def tableau_credentials_configured(self) -> bool:
        """Enough to sign in, test the connection and list projects (no target project yet)."""
        if self.tableau_mode == "mock":
            return self.tableau_enabled
        return bool(self.tableau_enabled and self.tableau_server_url and self.tableau_pat_name and self.tableau_pat_secret.get_secret_value())

    @property
    def tableau_configured(self) -> bool:
        """Ready to publish: credentials plus a target project."""
        return self.tableau_credentials_configured and (self.tableau_mode == "mock" or bool(self.tableau_project_id))


@lru_cache
def get_settings() -> Settings:
    return Settings()
