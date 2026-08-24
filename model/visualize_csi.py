"""Render one CSI window and optional checkpoint prediction as a PNG dashboard."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from model.care_signal_contract import care_signal_state
from model.csi_project.models import build_model
from model.infer_to_gateway import load_windows


def predict(checkpoint_path: Path, window: np.ndarray, device_name: str) -> tuple[list[str], np.ndarray]:
    checkpoint = torch.load(checkpoint_path, map_location=device_name, weights_only=True)
    metadata = checkpoint.get("metadata", {})
    class_names = list(checkpoint.get("class_names") or [])
    model_name = metadata.get("model_name")
    num_subcarriers = int(metadata.get("num_subcarriers", 0))
    mean = np.asarray(metadata.get("normalization_mean"), dtype=np.float32)
    std = np.asarray(metadata.get("normalization_std"), dtype=np.float32)
    if not class_names or not model_name or window.shape[1] != num_subcarriers:
        raise ValueError("checkpoint metadata does not match the CSI window")
    if mean.shape != (num_subcarriers,) or std.shape != (num_subcarriers,):
        raise ValueError("checkpoint normalization statistics are missing")

    normalized = (window - mean[None, :]) / np.maximum(std[None, :], 1e-6)
    device = torch.device(device_name)
    model = build_model(model_name, num_subcarriers, len(class_names)).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(normalized[None, ...]).to(device))
        scores = torch.softmax(logits, dim=1)[0].cpu().numpy()
    return class_names, scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--output", default="model/outputs/csi_visualization.png")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--x-key", default="X")
    parser.add_argument("--x-order", default="NTS")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    windows = load_windows(Path(args.input), args.x_key, args.x_order)
    if not 0 <= args.index < len(windows):
        raise ValueError(f"index must be between 0 and {len(windows) - 1}")
    window = windows[args.index]
    mean_amplitude = window.mean(axis=1)
    carrier_mean = window.mean(axis=0)

    fig = plt.figure(figsize=(14, 8), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=(1.25, 1))
    heatmap_axis = fig.add_subplot(grid[0, :])
    time_axis = fig.add_subplot(grid[1, 0])
    summary_axis = fig.add_subplot(grid[1, 1])

    image = heatmap_axis.imshow(window.T, aspect="auto", origin="lower", cmap="viridis")
    heatmap_axis.set_title(f"CSI amplitude heatmap — window {args.index}")
    heatmap_axis.set_xlabel("Time sample")
    heatmap_axis.set_ylabel("Subcarrier")
    fig.colorbar(image, ax=heatmap_axis, label="Amplitude")

    time_axis.plot(mean_amplitude, color="#006d77", linewidth=2)
    time_axis.set_title("Mean amplitude over time")
    time_axis.set_xlabel("Time sample")
    time_axis.set_ylabel("Mean amplitude")
    time_axis.grid(alpha=0.25)

    if args.checkpoint:
        class_names, scores = predict(Path(args.checkpoint), window, args.device)
        states = [care_signal_state(name) for name in class_names]
        colors = ["#94a3b8", "#22c55e", "#ef4444", "#f59e0b"]
        summary_axis.barh(states, scores, color=colors[: len(states)])
        summary_axis.set_xlim(0, 1)
        summary_axis.set_title(f"Model probabilities — {states[int(scores.argmax())]}")
        summary_axis.set_xlabel("Probability")
        for row, score in enumerate(scores):
            summary_axis.text(min(float(score) + 0.02, 0.94), row, f"{score:.1%}", va="center")
    else:
        summary_axis.bar(np.arange(len(carrier_mean)), carrier_mean, color="#3b82f6")
        summary_axis.set_title("Mean amplitude by subcarrier")
        summary_axis.set_xlabel("Subcarrier")
        summary_axis.set_ylabel("Mean amplitude")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    print(f"visualized=1 output={output.resolve()}")


if __name__ == "__main__":
    main()
