# 모델별로 동일하게 사용하는 학습/평가 엔진

from __future__ import annotations

import copy
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from torch import Tensor, nn
from torch.nn.utils import clip_grad_norm_
from torch.optim import AdamW
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import DataLoader

from .models import count_trainable_parameters


@dataclass(frozen=True)
class TrainConfig:
    epochs: int = 30
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 7
    gradient_clip: float = 5.0
    use_class_weights: bool = True


@dataclass
class TrainResult:
    metrics: dict[str, Any]
    history: list[dict[str, float]]
    confusion: np.ndarray


def set_global_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def resolve_device(requested: str) -> torch.device:
    requested = requested.lower()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA를 요청했지만 현재 PyTorch에서 사용할 수 없습니다.")
    return device


def _make_class_weights(
    train_loader: DataLoader[tuple[Tensor, Tensor]],
    num_classes: int,
    device: torch.device,
) -> Tensor:
    dataset = train_loader.dataset
    labels = torch.stack([dataset[index][1] for index in range(len(dataset))])
    counts = torch.bincount(labels, minlength=num_classes).float().clamp_min(1.0)
    weights = counts.sum() / (num_classes * counts)
    return weights.to(device)


def _run_epoch(
    model: nn.Module,
    loader: DataLoader[tuple[Tensor, Tensor]],
    criterion: nn.Module,
    device: torch.device,
    optimizer: AdamW | None = None,
    gradient_clip: float = 5.0,
) -> tuple[float, np.ndarray, np.ndarray]:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    all_true: list[np.ndarray] = []
    all_predicted: list[np.ndarray] = []

    context = torch.enable_grad() if training else torch.inference_mode()
    with context:
        for x, y in loader:
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            if training:
                optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = criterion(logits, y)
            if training:
                loss.backward()
                clip_grad_norm_(model.parameters(), max_norm=gradient_clip)
                optimizer.step()

            total_loss += float(loss.item()) * y.shape[0]
            all_true.append(y.detach().cpu().numpy())
            all_predicted.append(logits.argmax(dim=1).detach().cpu().numpy())

    true = np.concatenate(all_true)
    predicted = np.concatenate(all_predicted)
    return total_loss / len(loader.dataset), true, predicted


def _macro_f1(true: np.ndarray, predicted: np.ndarray, num_classes: int) -> float:
    _, _, f1, _ = precision_recall_fscore_support(
        true,
        predicted,
        labels=np.arange(num_classes),
        average=None,
        zero_division=0,
    )
    return float(np.mean(f1))


def _measure_latency(
    model: nn.Module,
    loader: DataLoader[tuple[Tensor, Tensor]],
    device: torch.device,
    repeats: int = 10,
) -> float:
    model.eval()
    x, _ = next(iter(loader))
    x = x.to(device)
    with torch.inference_mode():
        for _ in range(3):
            model(x)
        if device.type == "cuda":
            torch.cuda.synchronize()
        start = time.perf_counter()
        for _ in range(repeats):
            model(x)
        if device.type == "cuda":
            torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    return 1000.0 * elapsed / (repeats * x.shape[0])


def train_model(
    model: nn.Module,
    loaders: dict[str, DataLoader[tuple[Tensor, Tensor]]],
    class_names: list[str],
    config: TrainConfig,
    device: torch.device,
    checkpoint_path: str | Path,
    checkpoint_metadata: dict[str, Any] | None = None,
) -> TrainResult:
    model = model.to(device)
    num_classes = len(class_names)
    class_weights = (
        _make_class_weights(loaders["train"], num_classes, device)
        if config.use_class_weights
        else None
    )
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scheduler = ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=2)

    history: list[dict[str, float]] = []
    best_state: dict[str, Tensor] | None = None
    best_validation_f1 = -np.inf
    best_epoch = 0
    epochs_without_improvement = 0
    training_start = time.perf_counter()

    for epoch in range(1, config.epochs + 1):
        train_loss, train_true, train_predicted = _run_epoch(
            model,
            loaders["train"],
            criterion,
            device,
            optimizer=optimizer,
            gradient_clip=config.gradient_clip,
        )
        validation_loss, validation_true, validation_predicted = _run_epoch(
            model,
            loaders["validation"],
            criterion,
            device,
        )
        train_f1 = _macro_f1(train_true, train_predicted, num_classes)
        validation_f1 = _macro_f1(
            validation_true,
            validation_predicted,
            num_classes,
        )
        scheduler.step(validation_loss)
        history.append(
            {
                "epoch": float(epoch),
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
                "train_loss": train_loss,
                "train_macro_f1": train_f1,
                "validation_loss": validation_loss,
                "validation_macro_f1": validation_f1,
            }
        )

        if validation_f1 > best_validation_f1 + 1e-5:
            best_validation_f1 = validation_f1
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.patience:
                break

    training_seconds = time.perf_counter() - training_start
    if best_state is None:
        raise RuntimeError("유효한 checkpoint를 만들지 못했습니다.")
    model.load_state_dict(best_state)

    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": best_state,
            "train_config": asdict(config),
            "best_epoch": best_epoch,
            "class_names": class_names,
            "metadata": checkpoint_metadata or {},
        },
        checkpoint_path,
    )

    test_loss, test_true, test_predicted = _run_epoch(
        model,
        loaders["test"],
        criterion,
        device,
    )
    precision, recall, f1, support = precision_recall_fscore_support(
        test_true,
        test_predicted,
        labels=np.arange(num_classes),
        average=None,
        zero_division=0,
    )
    confusion = confusion_matrix(
        test_true,
        test_predicted,
        labels=np.arange(num_classes),
    )
    metrics: dict[str, Any] = {
        "test_loss": test_loss,
        "accuracy": float(accuracy_score(test_true, test_predicted)),
        "balanced_accuracy": float(
            balanced_accuracy_score(test_true, test_predicted)
        ),
        "macro_f1": float(np.mean(f1)),
        "best_validation_macro_f1": float(best_validation_f1),
        "best_epoch": best_epoch,
        "epochs_ran": len(history),
        "trainable_parameters": count_trainable_parameters(model),
        "inference_ms_per_sample": _measure_latency(
            model,
            loaders["test"],
            device,
        ),
        "training_seconds": training_seconds,
        "per_class": {
            name: {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, name in enumerate(class_names)
        },
    }
    return TrainResult(metrics=metrics, history=history, confusion=confusion)
