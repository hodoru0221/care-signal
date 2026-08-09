"""Evaluate model prediction JSONL without third-party dependencies.

Each line must contain ``ground_truth`` and ``state``. Optional
``captured_at`` and ``received_at`` timestamps are used for latency metrics.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

from backend.domain import VALID_STATES


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def evaluate_records(records: list[dict]) -> dict:
    labels = sorted(VALID_STATES)
    matrix = {truth: {prediction: 0 for prediction in labels} for truth in labels}
    latencies_ms: list[float] = []
    valid_count = 0

    for index, record in enumerate(records, start=1):
        truth = record.get("ground_truth")
        prediction = record.get("state")
        if truth not in VALID_STATES or prediction not in VALID_STATES:
            raise ValueError(f"line {index}: ground_truth and state must use standard states")
        matrix[truth][prediction] += 1
        valid_count += 1

        if record.get("captured_at") and record.get("received_at"):
            latency = (_parse_time(record["received_at"]) - _parse_time(record["captured_at"])).total_seconds() * 1000
            if latency >= 0:
                latencies_ms.append(latency)

    if valid_count == 0:
        raise ValueError("at least one evaluation record is required")

    per_class = {}
    for label in labels:
        true_positive = matrix[label][label]
        false_positive = sum(matrix[truth][label] for truth in labels if truth != label)
        false_negative = sum(matrix[label][prediction] for prediction in labels if prediction != label)
        support = sum(matrix[label].values())
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    correct = sum(matrix[label][label] for label in labels)
    macro_f1 = sum(item["f1"] for item in per_class.values()) / len(labels)
    return {
        "samples": valid_count,
        "accuracy": round(correct / valid_count, 4),
        "macro_f1": round(macro_f1, 4),
        "labels": labels,
        "confusion_matrix": matrix,
        "per_class": per_class,
        "latency_ms": {
            "samples": len(latencies_ms),
            "median": None if not latencies_ms else round(median(latencies_ms), 2),
            "p95": None if not latencies_ms else round(_percentile(latencies_ms, 0.95), 2),
            "max": None if not latencies_ms else round(max(latencies_ms), 2),
        },
    }


def load_jsonl(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: each record must be an object")
        records.append(value)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Care Signal model JSONL")
    parser.add_argument("input", type=Path, help="JSONL with ground_truth and state")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = evaluate_records(load_jsonl(args.input))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
