"""Normalize model JSONL output into the Care Signal observation contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VALID_STATES = {"EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"}
LABEL_ALIASES = {
    "EMPTY": "EMPTY",
    "VACANT": "EMPTY",
    "UNOCCUPIED": "EMPTY",
    "IN_BED": "IN_BED",
    "IN BED": "IN_BED",
    "LYING": "IN_BED",
    "OUT_OF_BED": "OUT_OF_BED",
    "OUT OF BED": "OUT_OF_BED",
    "OOB": "OUT_OF_BED",
    "FALL": "MOVEMENT_ANOMALY",
    "FALLEN": "MOVEMENT_ANOMALY",
    "ANOMALY": "MOVEMENT_ANOMALY",
    "MOVEMENT_ANOMALY": "MOVEMENT_ANOMALY",
}


def utc_iso(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def observation_id(payload: dict[str, Any]) -> str:
    """Create a stable ID from source identity fields (never from file position)."""
    identity = {key: payload.get(key) for key in (
        "room_id", "device_id", "captured_at", "model_version", "sequence_no"
    )}
    identity["state"] = payload.get("state")
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"obs-{digest}"


def normalize(raw: dict[str, Any], *, room_id: str | None = None,
              device_id: str | None = None, model_version: str | None = None) -> dict[str, Any]:
    label = raw.get("state", raw.get("label", raw.get("class")))
    normalized_label = str(label).strip().upper().replace("-", "_")
    state = LABEL_ALIASES.get(normalized_label)
    if state not in VALID_STATES:
        raise ValueError(f"unsupported model label: {label!r}")
    result: dict[str, Any] = {
        "room_id": raw.get("room_id", room_id),
        "device_id": raw.get("device_id", device_id),
        "state": state,
        "confidence": float(raw.get("confidence", raw.get("score"))),
        "captured_at": utc_iso(raw.get("captured_at", raw.get("timestamp"))),
        "model_version": raw.get("model_version", model_version),
    }
    if not result["room_id"] or not result["device_id"] or not result["model_version"]:
        raise ValueError("room_id, device_id and model_version are required")
    if not 0 <= result["confidence"] <= 1:
        raise ValueError("confidence must be between 0 and 1")
    sequence = raw.get("sequence_no", raw.get("sequence"))
    if sequence is not None:
        result["sequence_no"] = int(sequence)
    result["observation_id"] = str(raw.get("observation_id") or observation_id(result))
    return result


def bridge_lines(lines: Iterable[str], output: Path, dead_letter: Path, **defaults: str) -> tuple[int, int]:
    accepted = rejected = 0
    with output.open("a", encoding="utf-8") as destination, dead_letter.open("a", encoding="utf-8") as dead:
        for line in lines:
            if not line.strip():
                continue
            try:
                item = normalize(json.loads(line), **defaults)
                destination.write(json.dumps(item, separators=(",", ":")) + "\n")
                destination.flush()
                accepted += 1
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                dead.write(json.dumps({"line": line.rstrip("\r\n"), "error": str(exc)}) + "\n")
                dead.flush()
                rejected += 1
    return accepted, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="inference.jsonl")
    parser.add_argument("--dead-letter", default="model_bridge.dead.jsonl")
    parser.add_argument("--room-id")
    parser.add_argument("--device-id")
    parser.add_argument("--model-version")
    args = parser.parse_args()
    with Path(args.input).open(encoding="utf-8") as source:
        accepted, rejected = bridge_lines(source, Path(args.output), Path(args.dead_letter),
                                          room_id=args.room_id, device_id=args.device_id,
                                          model_version=args.model_version)
    print(f"normalized={accepted} rejected={rejected}")


if __name__ == "__main__":
    main()
