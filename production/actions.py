"""Operational action loop: insight -> assigned task -> observed outcome."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from .causal import ExperimentSpec, analyze_continuous


@dataclass
class OperationalAction:
    tenant_id: str
    store_id: str
    title: str
    problem: str
    intervention: str
    owner: str
    due_date: str
    success_metric: str
    hypothesis: str
    source_recommendation_id: str | None = None
    id: str = field(default_factory=lambda: "act_" + uuid4().hex[:10])
    status: str = "OPEN"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    outcome: dict | None = None


class ActionStore:
    def __init__(self):
        self._items: dict[str, OperationalAction] = {}
        self._lock = RLock()

    def create(self, action: OperationalAction) -> dict:
        with self._lock:
            self._items[action.id] = action
        return asdict(action)

    def list(self, tenant_id: str, store_id: str) -> list[dict]:
        with self._lock:
            return [asdict(item) for item in self._items.values() if item.tenant_id == tenant_id and item.store_id == store_id]

    def get(self, action_id: str) -> OperationalAction:
        with self._lock:
            if action_id not in self._items:
                raise KeyError(action_id)
            return self._items[action_id]

    def complete(self, action_id: str) -> dict:
        with self._lock:
            action = self.get(action_id)
            action.status = "AWAITING_MEASUREMENT"
            action.completed_at = datetime.now(timezone.utc).isoformat()
            return asdict(action)

    def measure(self, action_id: str, baseline: list[float], after: list[float], *, minimum_sample_per_arm: int = 30) -> dict:
        with self._lock:
            action = self.get(action_id)
            spec = ExperimentSpec(action.title, action.hypothesis, action.success_metric, "decrease" if "reduce" in action.hypothesis.lower() or "congestion" in action.success_metric.lower() else "increase", minimum_sample_per_arm)
            result = analyze_continuous(spec, baseline, after)
            action.outcome = result
            action.status = "MEASURED" if result["sample_sufficient"] else "AWAITING_MORE_DATA"
            return asdict(action)
