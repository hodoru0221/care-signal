# 세 모델을 동일한 CSI split과 학습 조건으로 비교

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from model.csi_project.data import (
    CSIDataBundle,
    compute_normalization_stats,
    load_csi_dataset,
    make_data_split,
    make_dataloaders,
    make_synthetic_csi,
)
from model.csi_project.engine import (
    TrainConfig,
    resolve_device,
    set_global_seed,
    train_model,
)
from model.csi_project.models import MODEL_NAMES, build_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plain CNN, CNN-BiLSTM-Attention, TCN-Attention 비교"
    )
    parser.add_argument(
        "--data",
        default="synthetic",
        help="synthetic 또는 .npz/.mat 파일 경로",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["all"],
        choices=["all", *MODEL_NAMES],
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0")
    parser.add_argument("--output-dir", default="outputs/default_run")
    parser.add_argument("--no-class-weights", action="store_true")

    parser.add_argument("--x-key", default="X")
    parser.add_argument("--y-key", default="y")
    parser.add_argument("--group-key", default="groups")
    parser.add_argument("--x-order", default="NTS")
    parser.add_argument("--class-names", nargs="*")

    parser.add_argument("--samples-per-class", type=int, default=120)
    parser.add_argument("--sequence-length", type=int, default=128)
    parser.add_argument("--num-subcarriers", type=int, default=30)
    parser.add_argument("--num-groups", type=int, default=20)
    parser.add_argument("--data-seed", type=int, default=2026)
    return parser.parse_args()


def load_bundle(args: argparse.Namespace) -> CSIDataBundle:
    supplied_names = args.class_names if args.class_names else None
    if args.data.lower() == "synthetic":
        bundle = make_synthetic_csi(
            samples_per_class=args.samples_per_class,
            sequence_length=args.sequence_length,
            num_subcarriers=args.num_subcarriers,
            num_groups=args.num_groups,
            seed=args.data_seed,
        )
        if supplied_names is not None:
            if len(supplied_names) != bundle.num_classes:
                raise ValueError("합성 데이터의 class name은 네 개여야 합니다.")
            bundle = CSIDataBundle(
                x=bundle.x,
                y=bundle.y,
                groups=bundle.groups,
                class_names=supplied_names,
                label_mapping=bundle.label_mapping,
            )
        return bundle

    return load_csi_dataset(
        args.data,
        x_key=args.x_key,
        y_key=args.y_key,
        group_key=args.group_key or None,
        x_order=args.x_order,
        class_names=supplied_names,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_confusion_matrix(
    confusion: np.ndarray,
    class_names: list[str],
    output_stem: Path,
) -> None:
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        output_stem.with_suffix(".csv"),
        confusion,
        delimiter=",",
        fmt="%d",
    )
    fig, axis = plt.subplots(figsize=(6.2, 5.2))
    image = axis.imshow(confusion, interpolation="nearest", cmap="Blues")
    fig.colorbar(image, ax=axis)
    axis.set(
        xticks=np.arange(len(class_names)),
        yticks=np.arange(len(class_names)),
        xticklabels=class_names,
        yticklabels=class_names,
        xlabel="Predicted label",
        ylabel="True label",
        title="Confusion matrix",
    )
    plt.setp(axis.get_xticklabels(), rotation=30, ha="right")
    threshold = confusion.max() / 2.0 if confusion.size else 0.0
    for row in range(confusion.shape[0]):
        for column in range(confusion.shape[1]):
            axis.text(
                column,
                row,
                str(confusion[row, column]),
                ha="center",
                va="center",
                color="white" if confusion[row, column] > threshold else "black",
            )
    fig.tight_layout()
    fig.savefig(output_stem.with_suffix(".png"), dpi=180)
    plt.close(fig)


def flatten_metrics(
    model_name: str,
    seed: int,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "model": model_name,
        "seed": seed,
        **{key: value for key, value in metrics.items() if key != "per_class"},
    }
    for class_name, class_metrics in metrics["per_class"].items():
        safe_name = class_name.replace(" ", "_")
        for metric_name, value in class_metrics.items():
            row[f"{safe_name}_{metric_name}"] = value
    return row


def aggregate_results(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metric_names = (
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "trainable_parameters",
        "inference_ms_per_sample",
        "training_seconds",
    )
    summary: list[dict[str, Any]] = []
    for model_name in MODEL_NAMES:
        model_rows = [row for row in raw_rows if row["model"] == model_name]
        if not model_rows:
            continue
        row: dict[str, Any] = {"model": model_name, "runs": len(model_rows)}
        for metric_name in metric_names:
            values = np.asarray(
                [float(result[metric_name]) for result in model_rows],
                dtype=np.float64,
            )
            row[f"{metric_name}_mean"] = float(values.mean())
            row[f"{metric_name}_std"] = float(values.std(ddof=1)) if len(values) > 1 else 0.0
        recall_keys = sorted(
            key for key in model_rows[0] if key.endswith("_recall")
        )
        for recall_key in recall_keys:
            values = np.asarray([float(result[recall_key]) for result in model_rows])
            row[f"{recall_key}_mean"] = float(values.mean())
            row[f"{recall_key}_std"] = (
                float(values.std(ddof=1)) if len(values) > 1 else 0.0
            )
        summary.append(row)
    return summary


def print_summary(rows: list[dict[str, Any]]) -> None:
    print("\nModel comparison")
    print(f"{'Model':28s} {'Accuracy':>10s} {'Macro-F1':>10s} {'Params':>12s} {'ms/sample':>12s}")
    for row in rows:
        print(
            f"{row['model']:28s} "
            f"{row['accuracy_mean']:10.4f} "
            f"{row['macro_f1_mean']:10.4f} "
            f"{row['trainable_parameters_mean']:12.0f} "
            f"{row['inference_ms_per_sample_mean']:12.4f}"
        )


def main() -> None:
    args = parse_args()
    selected_models = list(MODEL_NAMES) if "all" in args.models else args.models
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = load_bundle(args)
    device = resolve_device(args.device)

    run_metadata = {
        "arguments": vars(args),
        "dataset": {
            "num_samples": bundle.num_samples,
            "sequence_length": bundle.sequence_length,
            "num_subcarriers": bundle.num_subcarriers,
            "num_classes": bundle.num_classes,
            "class_names": bundle.class_names,
            "label_mapping": bundle.label_mapping,
            "group_split_available": bundle.groups is not None,
        },
        "device": str(device),
    }
    with (output_dir / "run_metadata.json").open("w", encoding="utf-8") as file:
        json.dump(run_metadata, file, ensure_ascii=False, indent=2)

    config = TrainConfig(
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        patience=args.patience,
        use_class_weights=not args.no_class_weights,
    )
    raw_rows: list[dict[str, Any]] = []

    print(
        f"Dataset: X={tuple(bundle.x.shape)}, classes={bundle.class_names}, device={device}"
    )
    for seed in args.seeds:
        split = make_data_split(bundle, seed=seed)
        np.savez(
            output_dir / f"split_seed_{seed}.npz",
            train=split.train,
            validation=split.validation,
            test=split.test,
        )
        stats = compute_normalization_stats(bundle, split.train)

        for model_name in selected_models:
            print(f"\n[{model_name}] seed={seed}")
            set_global_seed(seed)
            loaders = make_dataloaders(
                bundle,
                split,
                stats,
                batch_size=args.batch_size,
                seed=seed,
                num_workers=args.num_workers,
                pin_memory=device.type == "cuda",
            )
            model = build_model(
                model_name,
                num_subcarriers=bundle.num_subcarriers,
                num_classes=bundle.num_classes,
            )
            result = train_model(
                model,
                loaders,
                class_names=bundle.class_names,
                config=config,
                device=device,
                checkpoint_path=(
                    output_dir / "checkpoints" / f"{model_name}_seed_{seed}.pt"
                ),
                checkpoint_metadata={
                    "model_name": model_name,
                    "num_subcarriers": bundle.num_subcarriers,
                    "num_classes": bundle.num_classes,
                    "normalization_mean": stats.mean.tolist(),
                    "normalization_std": stats.std.tolist(),
                    "label_mapping": bundle.label_mapping,
                },
            )
            history_rows = [
                {"model": model_name, "seed": seed, **history}
                for history in result.history
            ]
            write_csv(
                output_dir / "history" / f"{model_name}_seed_{seed}.csv",
                history_rows,
            )
            save_confusion_matrix(
                result.confusion,
                bundle.class_names,
                output_dir / "confusion_matrices" / f"{model_name}_seed_{seed}",
            )
            raw_rows.append(flatten_metrics(model_name, seed, result.metrics))
            print(
                f"accuracy={result.metrics['accuracy']:.4f}, "
                f"macro_f1={result.metrics['macro_f1']:.4f}, "
                f"best_epoch={result.metrics['best_epoch']}"
            )

    summary_rows = aggregate_results(raw_rows)
    write_csv(output_dir / "results_raw.csv", raw_rows)
    write_csv(output_dir / "results_summary.csv", summary_rows)
    with (output_dir / "results.json").open("w", encoding="utf-8") as file:
        json.dump(
            {"raw": raw_rows, "summary": summary_rows},
            file,
            ensure_ascii=False,
            indent=2,
        )
    print_summary(summary_rows)
    print(f"\nSaved results to: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
