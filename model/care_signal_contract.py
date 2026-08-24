"""Map CSI classifier labels to the Care Signal observation contract."""
from __future__ import annotations


CSI_TO_CARE_SIGNAL = {
    "empty": "EMPTY",
    "still": "IN_BED",
    "moving": "MOVEMENT_ANOMALY",
    "bed_exit": "OUT_OF_BED",
}


def care_signal_state(label: str) -> str:
    """Return the server state for one classifier label."""
    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return CSI_TO_CARE_SIGNAL[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported CSI model label: {label!r}") from exc
