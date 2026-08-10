"""Explainable scoring + capped feedback weight updates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from packages.schemas import (
    CompetitiveEvent,
    EventType,
    FeedbackLabel,
    ScoreBreakdown,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "pricing_change": 0.85,
    "plan_change": 0.75,
    "feature_launch": 0.70,
    "feature_removal": 0.65,
    "positioning_change": 0.45,
    "blog_announcement": 0.35,
    "changelog_entry": 0.40,
    "other": 0.25,
    "noise": 0.05,
    "feature_overlap": 0.15,
    "icp_relevance": 0.12,
    "user_importance": 0.18,
    "confidence_factor": 0.10,
    "corroboration_boost": 0.08,
    "noise_penalty": 0.25,
}

WEIGHT_MIN = 0.05
WEIGHT_MAX = 0.95
FEEDBACK_DELTA = 0.03  # capped small updates


def clamp(v: float, lo: float = WEIGHT_MIN, hi: float = WEIGHT_MAX) -> float:
    return max(lo, min(hi, v))


def score_event(
    event: CompetitiveEvent,
    weights: dict[str, float] | None = None,
    *,
    feature_overlap: float = 0.0,
    icp_relevance: float = 0.0,
    user_importance: float = 0.0,
    corroboration: float = 0.0,
    is_noise_pattern: bool = False,
) -> ScoreBreakdown:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    et = event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)
    base = w.get(et, w["other"])

    pricing_signal = 0.0
    ents = event.extracted_entities
    if et == "pricing_change" or ents.old_price or ents.new_price:
        pricing_signal = 0.2 if ents.old_price and ents.new_price else 0.1

    conf = event.confidence
    noise_pen = w["noise_penalty"] if (et == "noise" or is_noise_pattern) else 0.0

    parts = {
        "event_type_weight": base,
        "pricing_signal": pricing_signal,
        "feature_overlap": feature_overlap * w["feature_overlap"],
        "icp_relevance": icp_relevance * w["icp_relevance"],
        "user_importance": user_importance * w["user_importance"],
        "confidence_factor": conf * w["confidence_factor"],
        "corroboration_boost": corroboration * w["corroboration_boost"],
        "noise_penalty": -noise_pen if noise_pen else 0.0,
    }
    total = sum(parts.values())
    total = clamp(total, 0.0, 1.0)

    explanation = [
        f"event_type `{et}` base={base:.2f}",
        f"confidence={conf:.2f} → +{parts['confidence_factor']:.2f}",
    ]
    if pricing_signal:
        explanation.append(f"pricing entities detected → +{pricing_signal:.2f}")
    if feature_overlap:
        explanation.append(f"feature overlap {feature_overlap:.2f} → +{parts['feature_overlap']:.2f}")
    if icp_relevance:
        explanation.append(f"ICP relevance {icp_relevance:.2f} → +{parts['icp_relevance']:.2f}")
    if user_importance:
        explanation.append(f"user importance factors {user_importance:.2f} → +{parts['user_importance']:.2f}")
    if corroboration:
        explanation.append(f"multi-source corroboration → +{parts['corroboration_boost']:.2f}")
    if noise_pen:
        explanation.append(f"noise penalty → {parts['noise_penalty']:.2f}")
    explanation.append(f"total score={total:.2f}")

    return ScoreBreakdown(
        **parts,
        total=round(total, 4),
        explanation=explanation,
        weights_used={k: w[k] for k in w},
    )


def propose_weight_update(
    current: dict[str, float],
    label: FeedbackLabel,
    event_type: str,
    breakdown: dict[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Return (new_weights, deltas, human_readable_explanations)."""
    new_w = deepcopy({**DEFAULT_WEIGHTS, **current})
    deltas: dict[str, float] = {}
    notes: list[str] = []

    key = event_type if event_type in new_w else "other"
    if label == FeedbackLabel.useful:
        deltas[key] = FEEDBACK_DELTA
        notes.append(f"useful → increase `{key}` by +{FEEDBACK_DELTA:.2f}")
    elif label == FeedbackLabel.noise:
        deltas[key] = -FEEDBACK_DELTA
        deltas["noise_penalty"] = FEEDBACK_DELTA
        notes.append(f"noise → decrease `{key}` by -{FEEDBACK_DELTA:.2f}, raise noise_penalty")
    else:  # meh
        deltas[key] = -FEEDBACK_DELTA / 2
        notes.append(f"meh → slight decrease `{key}` by {-FEEDBACK_DELTA/2:.2f}")

    for k, d in deltas.items():
        before = new_w.get(k, DEFAULT_WEIGHTS.get(k, 0.3))
        after = clamp(before + d)
        new_w[k] = round(after, 4)
        notes.append(f"{k}: {before:.2f} → {after:.2f}")

    return new_w, deltas, notes


def gate_decision(
    score: float,
    confidence: float,
    threshold: float,
    review_low: float,
    review_high: float,
) -> str:
    """Return: alert | review | suppress."""
    if score >= threshold and confidence >= 0.45:
        return "alert"
    if review_low <= score < review_high or (score >= threshold and confidence < 0.45):
        return "review"
    return "suppress"
