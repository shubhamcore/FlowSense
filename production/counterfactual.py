"""Uncertainty-aware intervention ranking for the FlowSense Action Frontier.

This is deliberately not a revenue model. It ranks operational tests by the
strength of their observed evidence, expected directional effect, feasibility
and uncertainty so a store manager can choose the next experiment.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import log
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Intervention:
    id: str
    name: str
    mechanism: str
    expected_change_pct: float
    confidence: float
    feasibility: float
    effort: int
    guardrail: str
    evidence: tuple[str, ...]

    def score(self) -> float:
        # Impact × confidence × feasibility, penalized by operational effort.
        return round(abs(self.expected_change_pct) * self.confidence * self.feasibility / max(1, self.effort), 2)

    def result(self) -> dict:
        uncertainty = max(1.0, abs(self.expected_change_pct) * (1 - self.confidence) * 0.65)
        return {
            **asdict(self),
            "score": self.score(),
            "expected_range": [round(self.expected_change_pct - uncertainty, 1), round(self.expected_change_pct + uncertainty, 1)],
            "data_class": "SIMULATED",
            "claim_guardrail": "Directional hypothesis only; validate with a matched observed experiment.",
        }


def rank_interventions(items: Sequence[Intervention]) -> list[dict]:
    return sorted((item.result() for item in items), key=lambda result: result["score"], reverse=True)


def journey_entropy(pattern_counts: Mapping[str, int]) -> float:
    """Normalized path diversity; high entropy means less predictable flow."""
    total = sum(max(0, int(value)) for value in pattern_counts.values())
    if total <= 0 or len(pattern_counts) <= 1:
        return 0.0
    entropy = -sum((value / total) * log(value / total) for value in pattern_counts.values() if value > 0)
    return round(entropy / log(len(pattern_counts)), 4)


def active_learning_priority(*, confidence: float, affected_visitors: int, intervention_effort: int) -> dict:
    """Recommend where to collect more evidence before changing the store."""
    uncertainty = max(0.0, min(1.0, 1 - confidence))
    exposure = max(0.0, min(1.0, affected_visitors / 500))
    priority = round((uncertainty * .55 + exposure * .3 + (1 / max(1, intervention_effort)) * .15) * 100, 1)
    return {"priority": priority, "confidence_gap": round(uncertainty, 3), "reason": "Collect another matched window before acting" if priority >= 55 else "Current evidence is adequate for a low-risk test"}
