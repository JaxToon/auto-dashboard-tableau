"""Runtime feature flags, kill switches and thresholds, plus the service container.

Environment values are ceilings for feature flags: an admin can switch a feature off at
runtime, but cannot switch on something the deployment disabled.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import Catalog, get_catalog
from .config import Settings, get_settings
from .data_provider import SyntheticDataAdapter
from .db import RuntimeSetting
from .errors import FeatureDisabled
from .identity import UserContext
from .planner import LLMProvider, SemanticContext, get_provider
from .tableau_publisher import TableauPublisher, get_publisher

FLAG_KEYS = (
    "feature_ai_planning",
    "feature_data_access",
    "feature_dashboard_publish",
    "feature_recurring_recommendations",
    "feature_recommendation_telemetry",
)
VALUE_KEYS = ("review_required", "rec_min_events", "rec_window_days", "rec_min_actors")


@dataclass
class RuntimeConfig:
    settings: Settings
    overrides: dict = field(default_factory=dict)

    def load(self, db: Session) -> None:
        self.overrides = {r.key: r.value.get("v") for r in db.execute(select(RuntimeSetting)).scalars()}

    def flag(self, key: str) -> bool:
        env = bool(getattr(self.settings, key))
        return env and bool(self.overrides.get(key, True))

    def value(self, key: str):
        return self.overrides.get(key, getattr(self.settings, key))

    def require(self, key: str, label: str) -> None:
        if not self.flag(key):
            raise FeatureDisabled(f"{label} is currently disabled.", code=key.upper() + "_DISABLED")

    def snapshot(self) -> dict:
        return {
            "flags": {k: {"effective": self.flag(k), "env_ceiling": bool(getattr(self.settings, k))} for k in FLAG_KEYS},
            "values": {k: self.value(k) for k in VALUE_KEYS},
        }

    def update(self, db: Session, changes: dict, actor: str) -> dict:
        applied = {}
        for k, v in changes.items():
            if v is None or k not in FLAG_KEYS + VALUE_KEYS:
                continue
            row = db.get(RuntimeSetting, k)
            if row is None:
                db.add(RuntimeSetting(key=k, value={"v": v}, updated_by=actor))
            else:
                row.value = {"v": v}
                row.updated_by = actor
            self.overrides[k] = v
            applied[k] = v
        db.commit()
        return applied


class Container:
    def __init__(self, settings: Settings | None = None, catalog: Catalog | None = None):
        self.settings = settings or get_settings()
        self.catalog = catalog or get_catalog()
        self.runtime = RuntimeConfig(self.settings)
        self.data = SyntheticDataAdapter(
            self.catalog,
            self.settings.data_dir,
            max_rows=self.settings.max_result_rows,
            max_series=self.settings.max_series,
            enabled=lambda: self.runtime.flag("feature_data_access"),
        )
        self.planner: LLMProvider = get_provider(self.settings.llm_provider, enabled=lambda: self.runtime.flag("feature_ai_planning"))
        self.publisher: TableauPublisher = get_publisher(self.settings)
        self._cardinality = self._compute_cardinality()

    def _compute_cardinality(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ds in self.catalog.datasets:
            values: dict[str, set] = defaultdict(set)
            for row in self.data._data.get(ds.id, []):
                for f in ds.dimension_fields:
                    values[f.id].add(row.get(f.id))
            out.update({f"{ds.id}.{k}": len(v) for k, v in values.items()})
        return out

    @property
    def cardinality(self) -> dict[str, int]:
        return self._cardinality

    def coverage(self, dataset_id: str) -> tuple[date, date]:
        f = self.data.get_data_freshness(dataset_id)
        return date.fromisoformat(f["earliest_period"]), date.fromisoformat(f["latest_period"])

    def semantic_context(self, user: UserContext, selected_dataset_id: str | None = None) -> SemanticContext:
        accessible = self.catalog.accessible_datasets(user.roles)
        cov = {d.id: self.coverage(d.id) for d in self.catalog.datasets}
        return SemanticContext(
            catalog=self.catalog,
            accessible=accessible,
            latest_period={k: v[1] for k, v in cov.items()},
            earliest_period={k: v[0] for k, v in cov.items()},
            max_series=self.settings.max_series,
            selected_dataset_id=selected_dataset_id,
            dimension_cardinality=self.cardinality,
        )
