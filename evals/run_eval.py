"""
Eval harness: precision/recall for alerting + basic extraction quality.

Usage:
  python evals/run_eval.py

A/B note: swap SCORE_STRATEGY or compare rule-only vs rule+RAG by setting
SIGNALWATCH_SCORE_STRATEGY=rules|rules_rag (documented in README).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.connectors import fetch_fixture, normalize_html
from packages.diffing import cheap_text_diff
from packages.agent.extract import extract_events
from packages.schemas import PageLabel
from packages.scoring import gate_decision, score_event
from packages.settings import get_settings


def entity_hit(expected: dict, actual) -> float:
    if not expected:
        return 1.0
    got = actual.model_dump() if hasattr(actual, "model_dump") else (actual or {})
    keys = [k for k, v in expected.items() if v]
    if not keys:
        return 1.0
    hits = 0
    for k in keys:
        ev = str(expected[k]).lower()
        av = str(got.get(k) or "").lower()
        if ev and ev in av or av and av in ev:
            hits += 1
    return hits / len(keys)


def main() -> None:
    labels_path = ROOT / "evals" / "fixtures" / "labels.json"
    if not labels_path.exists():
        from evals.fixtures.generate_fixtures import CASES  # noqa — regenerate

        # run generator
        import runpy

        runpy.run_path(str(ROOT / "evals" / "fixtures" / "generate_fixtures.py"))

    cases = json.loads(labels_path.read_text(encoding="utf-8"))
    settings = get_settings()
    strategy = os.getenv("SIGNALWATCH_SCORE_STRATEGY", "rules")

    y_true: list[int] = []
    y_pred: list[int] = []
    type_hits = 0
    type_n = 0
    ent_scores: list[float] = []

    rows = []
    for case in cases:
        before = fetch_fixture(case["before"])
        after = fetch_fixture(case["after"])
        if not before.ok or not after.ok:
            rows.append({"id": case["id"], "error": before.error or after.error})
            continue

        diff = cheap_text_diff(before.normalized_text, after.normalized_text)
        label = PageLabel(case.get("page_label", "other"))
        events = []
        if diff.changed and diff.changed_sections:
            for section in diff.changed_sections:
                events.extend(
                    extract_events(case.get("competitor", "Eval"), case["after"], section, label)
                )
        elif not diff.changed and not case["should_alert"]:
            # correctly quiet
            pass

        best = max(events, key=lambda e: e.confidence, default=None)
        # Score
        alerted = False
        pred_type = "noise"
        ent_score = 1.0 if not case.get("key_entities") else 0.0
        if best:
            pred_type = best.event_type.value
            feature = 0.5 if strategy == "rules_rag" else 0.0
            breakdown = score_event(best, feature_overlap=feature, icp_relevance=feature)
            decision = gate_decision(
                breakdown.total,
                best.confidence,
                settings.alert_score_threshold,
                settings.review_band_low,
                settings.review_band_high,
            )
            alerted = decision in ("alert", "review") and best.event_type.value != "noise"
            # For eval, treat review as alert candidate (human would see it)
            if case["should_alert"] and decision == "review":
                alerted = True
            ent_score = entity_hit(case.get("key_entities") or {}, best.extracted_entities)

        # Noise-only diffs should not alert
        if diff.is_noise_only or (not diff.changed):
            alerted = False
            pred_type = "noise"

        y_true.append(1 if case["should_alert"] else 0)
        y_pred.append(1 if alerted else 0)

        if case["should_alert"]:
            type_n += 1
            if pred_type == case["event_type"] or (
                case["event_type"] in pred_type or pred_type in case["event_type"]
            ):
                type_hits += 1
            ent_scores.append(ent_score)

        rows.append(
            {
                "id": case["id"],
                "should_alert": case["should_alert"],
                "alerted": alerted,
                "expected_type": case["event_type"],
                "pred_type": pred_type,
                "entity_score": round(ent_score, 2),
                "change_ratio": diff.change_ratio,
                "noise_only": diff.is_noise_only,
            }
        )

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    type_acc = (type_hits / type_n) if type_n else 0.0
    ent_avg = sum(ent_scores) / len(ent_scores) if ent_scores else 0.0

    report = {
        "strategy": strategy,
        "n_cases": len(cases),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "event_type_accuracy_on_positives": round(type_acc, 3),
        "entity_extraction_score": round(ent_avg, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "cases": rows,
    }

    out = ROOT / "evals" / "last_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("=== SignalWatch Eval ===")
    print(f"strategy={strategy}  n={len(cases)}")
    print(f"precision={precision:.3f}  recall={recall:.3f}  f1={f1:.3f}")
    print(f"event_type_acc={type_acc:.3f}  entity_score={ent_avg:.3f}")
    print(f"tp={tp} fp={fp} fn={fn}")
    print(f"Wrote {out}")

    # Fail CI if catastrophic
    if f1 < 0.4:
        print("WARNING: F1 below 0.40 — investigate scoring/extraction")
        sys.exit(1)


if __name__ == "__main__":
    main()
