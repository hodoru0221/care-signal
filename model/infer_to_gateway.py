"""Run a trained CSI checkpoint and append Care Signal observations to JSONL."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from scipy.io import loadmat

from gateway.model_bridge import normalize
from model.care_signal_contract import care_signal_state
from model.csi_project.models import build_model


def load_windows(path: Path, key: str, order: str) -> np.ndarray:
    """Load CSI amplitude windows and normalize their axes to [N, T, S]."""
    if path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as data:
            if key not in data:
                raise ValueError(f"{path.name} does not contain {key!r}")
            windows = np.asarray(data[key], dtype=np.float32)
    elif path.suffix.lower() == ".mat":
        data = loadmat(path)
        if key not in data:
            raise ValueError(f"{path.name} does not contain {key!r}")
        windows = np.asarray(data[key], dtype=np.float32)
    else:
        raise ValueError("input must be an .npz or MATLAB -v7 .mat file")

    order = order.upper()
    if windows.ndim != 3 or sorted(order) != ["N", "S", "T"]:
        raise ValueError("X must be 3-dimensional and x-order must contain N, T and S")
    windows = np.transpose(windows, [order.index(axis) for axis in "NTS"])
    if not np.isfinite(windows).all():
        raise ValueError("X contains NaN or infinite values")
    return np.ascontiguousarray(windows)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="inference.jsonl")
    parser.add_argument("--x-key", default="X")
    parser.add_argument("--x-order", default="NTS")
    parser.add_argument("--room-id", default="room-01")
    parser.add_argument("--device-id", default="csi-receiver-01")
    parser.add_argument("--model-version")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    if args.batch_size < 1:
        raise ValueError("batch-size must be at least 1")
    checkpoint = torch.load(checkpoint_path, map_location=args.device, weights_only=True)
    metadata = checkpoint.get("metadata", {})
    class_names = checkpoint.get("class_names")
    model_name = metadata.get("model_name")
    num_subcarriers = int(metadata.get("num_subcarriers", 0))
    if not class_names or not model_name or not num_subcarriers:
        raise ValueError("checkpoint is missing class names or model metadata")

    windows = load_windows(Path(args.input), args.x_key, args.x_order)
    if windows.shape[2] != num_subcarriers:
        raise ValueError(
            f"checkpoint expects {num_subcarriers} subcarriers, got {windows.shape[2]}"
        )
    mean = np.asarray(metadata.get("normalization_mean"), dtype=np.float32)
    std = np.asarray(metadata.get("normalization_std"), dtype=np.float32)
    if mean.shape != (num_subcarriers,) or std.shape != (num_subcarriers,):
        raise ValueError("checkpoint is missing compatible normalization statistics")
    windows = (windows - mean[None, None, :]) / np.maximum(std[None, None, :], 1e-6)

    device = torch.device(args.device)
    model = build_model(model_name, num_subcarriers, len(class_names)).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    model_version = args.model_version or checkpoint_path.stem
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with output.open("a", encoding="utf-8") as destination, torch.inference_mode():
        for start in range(0, len(windows), args.batch_size):
            batch = torch.from_numpy(windows[start : start + args.batch_size]).to(device)
            probabilities = torch.softmax(model(batch), dim=1).cpu().numpy()
            for offset, scores in enumerate(probabilities):
                prediction = int(scores.argmax())
                sequence = start + offset
                observation = normalize(
                    {
                        "state": care_signal_state(class_names[prediction]),
                        "confidence": float(scores[prediction]),
                        "captured_at": utc_now(),
                        "sequence_no": sequence,
                    },
                    room_id=args.room_id,
                    device_id=args.device_id,
                    model_version=model_version,
                )
                destination.write(json.dumps(observation, separators=(",", ":")) + "\n")
                destination.flush()
                written += 1
    print(f"predicted={written} output={output.resolve()}")


if __name__ == "__main__":
    main()
