"""Causal experiment primitives for before/after layout tests.

This intentionally refuses to call a winner when sample size is too small. It
uses transparent normal-approximation intervals for a fast demo; production
should add CUPED, matched stores, pre-period covariates and a stats-reviewed
analysis plan.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt
from statistics import mean, variance
from typing import Sequence


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    hypothesis: str
    primary_metric: str
    direction: str  # decrease or increase
    minimum_sample_per_arm: int = 30
    guardrails: tuple[str, ...] = ()

    def as_dict(self):
        return asdict(self)


def minimum_sample_size_continuous(*, standard_deviation: float, minimum_detectable_effect: float, alpha: float = .05, power: float = .8) -> int:
    if standard_deviation <= 0 or minimum_detectable_effect <= 0:
        raise ValueError("Standard deviation and effect must be positive")
    # Normal critical values for a common two-sided 95% / 80% calculation.
    z_alpha = 1.96 if alpha <= .05 else 1.645
    z_power = 0.84 if power >= .8 else .52
    return max(2, int(round(2 * ((z_alpha + z_power) * standard_deviation / minimum_detectable_effect) ** 2)))


def _mean_var(values: Sequence[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (mean(values) if values else 0.0, 0.0)
    return mean(values), variance(values)


def analyze_continuous(spec: ExperimentSpec, baseline: Sequence[float], variant: Sequence[float]) -> dict:
    if not baseline or not variant:
        raise ValueError("Both baseline and variant samples are required")
    base_mean, base_var = _mean_var(baseline)
    var_mean, var_var = _mean_var(variant)
    effect = var_mean - base_mean
    se = sqrt((base_var / max(1, len(baseline))) + (var_var / max(1, len(variant))))
    margin = 1.96 * se
    ci = [round(effect - margin, 4), round(effect + margin, 4)]
    z = effect / se if se else 0.0
    significant = abs(z) >= 1.96
    enough = len(baseline) >= spec.minimum_sample_per_arm and len(variant) >= spec.minimum_sample_per_arm
    direction_ok = effect < 0 if spec.direction == "decrease" else effect > 0
    decision = "PROMOTE" if enough and significant and direction_ok else ("CONTINUE" if enough else "INSUFFICIENT_SAMPLE")
    relative_effect = effect / base_mean if base_mean else None
    return {
        "experiment": spec.as_dict(),
        "baseline": {"n": len(baseline), "mean": round(base_mean, 4), "std": round(sqrt(base_var), 4) if base_var else 0.0},
        "variant": {"n": len(variant), "mean": round(var_mean, 4), "std": round(sqrt(var_var), 4) if var_var else 0.0},
        "effect": {"absolute": round(effect, 4), "relative": round(relative_effect, 4) if relative_effect is not None else None, "confidence_interval_95": ci, "z_score": round(z, 4)},
        "significant_at_95": significant,
        "sample_sufficient": enough,
        "direction_supported": direction_ok,
        "decision": decision,
        "data_class": "MEASURED",
        "caveat": "Normal approximation; use a stats-reviewed matched or randomized design before making a business claim.",
    }


def preregister(spec: ExperimentSpec) -> dict:
    return {"status": "PREREGISTERED", "spec": spec.as_dict(), "primary_metric_locked": True, "created_by": "FlowSense experiment engine"}
