"""FlowSense moat: the store-specific Flow Genome.

A genome is not an identity profile. It is an aggregate behavioral fingerprint
of a store layout: path mix, journey entropy, dwell signature, congestion
signatures, dead-zone exposure and validated intervention outcomes. It is
scoped to a tenant/store and never contains individual trajectories.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from math import sqrt
from statistics import mean
from typing import Mapping, Sequence

from .counterfactual import journey_entropy


@dataclass(frozen=True)
class FlowGenome:
    store_id: str
    journey_entropy: float
    dominant_patterns: list[dict]
    dwell_signature: dict[str, float]
    congestion_signature: list[dict]
    dead_zone_signature: list[dict]
    validated_outcomes: list[dict]
    fingerprint_hash: str
    data_class: str = "ESTIMATED"

    def as_dict(self):
        return asdict(self)


def _pattern_key(journey: Mapping) -> str:
    if journey.get("pattern"):
        return str(journey["pattern"])
    zones = journey.get("zones", [])
    return " → ".join(str(x) for x in zones) if zones else "unknown"


def build_flow_genome(*, store_id: str, journeys: Sequence[Mapping], sections: Sequence[Mapping], bottlenecks: Sequence[Mapping], dead_zones: Sequence[Mapping], outcomes: Sequence[Mapping] = (), data_class: str = "ESTIMATED") -> FlowGenome:
    patterns = Counter(_pattern_key(journey) for journey in journeys)
    total = sum(patterns.values()) or 1
    dominant = [{"pattern": key, "share": round(value / total, 4), "count": value} for key, value in patterns.most_common(8)]
    dwell = {str(section.get("id", section.get("name", "unknown"))): round(float(section.get("dwell", section.get("avg_dwell", 0))), 2) for section in sections}
    congestion = sorted(({"zone": str(item.get("name", item.get("zone_id", "unknown"))), "score": round(float(item.get("score", 0)), 2), "severity": item.get("severity", "UNKNOWN")} for item in bottlenecks), key=lambda item: item["score"], reverse=True)[:5]
    dead = sorted(({"zone": str(item.get("name", item.get("zone_id", "unknown"))), "score": round(float(item.get("score", 0)), 2), "opportunity": round(float(item.get("opportunity", item.get("opportunity_score", 0))), 2)} for item in dead_zones), key=lambda item: item["score"], reverse=True)[:5]
    payload = {"store_id": store_id, "journey_entropy": journey_entropy(patterns), "dominant_patterns": dominant, "dwell_signature": dwell, "congestion_signature": congestion, "dead_zone_signature": dead, "validated_outcomes": list(outcomes)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return FlowGenome(**payload, fingerprint_hash="fg_" + digest, data_class=data_class)


def _vector(genome: Mapping) -> list[float]:
    congestion = genome.get("congestion_signature", [])
    dead = genome.get("dead_zone_signature", [])
    dwell = genome.get("dwell_signature", {})
    return [
        float(genome.get("journey_entropy", 0)),
        mean([float(item.get("score", 0)) for item in congestion]) / 100 if congestion else 0,
        mean([float(item.get("score", 0)) for item in dead]) / 100 if dead else 0,
        mean([float(value) for value in dwell.values()]) / 180 if dwell else 0,
    ]


def genome_similarity(left: Mapping, right: Mapping) -> float:
    a, b = _vector(left), _vector(right)
    dot = sum(x * y for x, y in zip(a, b))
    denom = sqrt(sum(x * x for x in a)) * sqrt(sum(y * y for y in b))
    cosine = dot / denom if denom else 0.0
    return round(max(0.0, min(1.0, (cosine + 1) / 2)), 4)


def peer_benchmark(genome: Mapping, peers: Sequence[Mapping]) -> dict:
    values = [float(peer.get("journey_entropy", 0)) for peer in peers]
    current = float(genome.get("journey_entropy", 0))
    rank = 1 + sum(value < current for value in values)
    percentile = round((rank / (len(values) + 1)) * 100, 1) if values else None
    return {"metric": "journey_entropy", "value": current, "peer_count": len(values), "percentile": percentile, "interpretation": "Lower entropy indicates a more predictable journey mix; it is not automatically better."}


def learn_from_peers(genome: Mapping, peer_genomes: Sequence[Mapping], *, minimum_similarity: float = .82) -> list[dict]:
    recommendations = []
    for peer in peer_genomes:
        similarity = genome_similarity(genome, peer)
        if similarity < minimum_similarity:
            continue
        for outcome in peer.get("validated_outcomes", []):
            if outcome.get("decision") != "PROMOTE":
                continue
            recommendations.append({"source_store": peer.get("store_id"), "similarity": similarity, "intervention": outcome.get("intervention", outcome.get("name")), "observed_effect": outcome.get("effect"), "transferability": "HYPOTHESIS", "guardrail": "Validate locally; peer success is not a causal guarantee."})
    return sorted(recommendations, key=lambda item: item["similarity"], reverse=True)[:5]
