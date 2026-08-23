from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from torch import Tensor
from torch.utils.data import DataLoader, Dataset


@dataclass(frozen=True)
class CSIDataBundle:
    x: Tensor
    y: Tensor
    groups: np.ndarray | None
    class_names: list[str]
    label_mapping: dict[str, int]

    @property
    def num_samples(self) -> int:
        return int(self.x.shape[0])

    @property
    def sequence_length(self) -> int:
        return int(self.x.shape[1])

    @property
    def num_subcarriers(self) -> int:
        return int(self.x.shape[2])

    @property
    def num_classes(self) -> int:
        return len(self.class_names)


@dataclass(frozen=True)
class DataSplit:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray


@dataclass(frozen=True)
class NormalizationStats:
    mean: Tensor
    std: Tensor


class IndexedNormalizedDataset(Dataset[tuple[Tensor, Tensor]]):
    def __init__(
        self,
        bundle: CSIDataBundle,
        indices: np.ndarray,
        stats: NormalizationStats,
    ) -> None:
        self.x = bundle.x
        self.y = bundle.y
        self.indices = torch.as_tensor(indices, dtype=torch.long)
        self.mean = stats.mean
        self.std = stats.std

    def __len__(self) -> int:
        return int(self.indices.numel())

    def __getitem__(self, item: int) -> tuple[Tensor, Tensor]:
        index = self.indices[item]
        sample = (self.x[index] - self.mean) / self.std
        return sample, self.y[index]


def _reorder_x(x: np.ndarray, x_order: str) -> np.ndarray:
    order = x_order.upper()
    if len(order) != 3 or set(order) != {"N", "T", "S"}:
        raise ValueError("x_order는 N, T, S를 한 번씩 포함해야 합니다. 예: NTS, TSN")
    permutation = (order.index("N"), order.index("T"), order.index("S"))
    return np.transpose(x, permutation)


def _encode_labels(raw_y: np.ndarray) -> tuple[np.ndarray, dict[str, int], list[str]]:
    y = np.asarray(raw_y).reshape(-1)
    unique_labels = np.unique(y)
    if unique_labels.size < 2:
        raise ValueError("분류를 위해 서로 다른 label이 두 개 이상 필요합니다.")
    encoded = np.searchsorted(unique_labels, y).astype(np.int64)
    class_names = [str(label) for label in unique_labels.tolist()]
    mapping = {name: index for index, name in enumerate(class_names)}
    return encoded, mapping, class_names


def load_csi_dataset(
    path: str | Path,
    x_key: str = "X",
    y_key: str = "y",
    group_key: str | None = "groups",
    x_order: str = "NTS",
    class_names: list[str] | None = None,
) -> CSIDataBundle:
    # NPZ 또는 MATLAB MAT 파일을 공통 [N, T, S] 형식으로 읽음

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".npz":
        loaded = np.load(path, allow_pickle=False)
        if x_key not in loaded or y_key not in loaded:
            raise KeyError(f"{path.name}에 '{x_key}'와 '{y_key}'가 모두 필요합니다.")
        raw_x = loaded[x_key]
        raw_y = loaded[y_key]
        raw_groups = loaded[group_key] if group_key and group_key in loaded else None
    elif path.suffix.lower() == ".mat":
        try:
            from scipy.io import loadmat
        except ImportError as error:
            raise ImportError("MAT 파일을 읽으려면 scipy가 필요합니다.") from error
        loaded = loadmat(path)
        if x_key not in loaded or y_key not in loaded:
            raise KeyError(f"{path.name}에 '{x_key}'와 '{y_key}'가 모두 필요합니다.")
        raw_x = loaded[x_key]
        raw_y = loaded[y_key]
        raw_groups = loaded.get(group_key) if group_key else None
    else:
        raise ValueError("현재 지원 형식은 .npz와 .mat입니다.")

    raw_x_array = np.asarray(raw_x)
    if np.iscomplexobj(raw_x_array):
        warnings.warn(
            "복소수 CSI 입력을 감지하여 amplitude=abs(CSI)로 변환합니다.",
            stacklevel=2,
        )
        raw_x_array = np.abs(raw_x_array)
    x = _reorder_x(raw_x_array, x_order).astype(np.float32, copy=False)
    y, label_mapping, inferred_names = _encode_labels(np.asarray(raw_y))
    if x.ndim != 3:
        raise ValueError(f"X는 3차원이어야 합니다. 현재 형상: {x.shape}")
    if x.shape[0] != y.shape[0]:
        raise ValueError(f"X sample 수 {x.shape[0]}와 y 길이 {y.shape[0]}가 다릅니다.")
    if not np.isfinite(x).all():
        raise ValueError("X에 NaN 또는 Inf가 있습니다. 전처리 후 다시 저장해야 합니다.")

    groups = None
    if raw_groups is not None:
        groups = np.asarray(raw_groups).reshape(-1)
        if groups.shape[0] != x.shape[0]:
            raise ValueError("groups 길이는 sample 수와 같아야 합니다.")

    names = inferred_names if class_names is None else class_names
    if len(names) != len(inferred_names):
        raise ValueError(
            f"class_names는 {len(inferred_names)}개여야 하지만 {len(names)}개입니다."
        )

    return CSIDataBundle(
        x=torch.from_numpy(np.ascontiguousarray(x)),
        y=torch.from_numpy(y),
        groups=groups,
        class_names=list(names),
        label_mapping=label_mapping,
    )


def make_synthetic_csi(
    samples_per_class: int = 120,
    sequence_length: int = 128,
    num_subcarriers: int = 30,
    num_groups: int = 20,
    seed: int = 2026,
) -> CSIDataBundle:

    if samples_per_class < num_groups:
        raise ValueError("samples_per_class는 num_groups 이상이어야 합니다.")
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 1.0, sequence_length, dtype=np.float32)
    s = np.linspace(0.0, 2.0 * np.pi, num_subcarriers, dtype=np.float32)
    class_names = ["empty", "still", "moving", "bed_exit"]

    samples: list[np.ndarray] = []
    labels: list[int] = []
    groups: list[int] = []

    for class_index in range(len(class_names)):
        for sample_index in range(samples_per_class):
            spatial_phase = rng.uniform(-np.pi, np.pi)
            spatial_profile = 1.0 + 0.20 * np.sin(s + spatial_phase)
            baseline = (
                rng.normal(0.0, 0.05)
                + 0.10 * np.sin(s + rng.uniform(-np.pi, np.pi))
            )[None, :]
            noise = rng.normal(
                0.0,
                0.025,
                size=(sequence_length, num_subcarriers),
            ).astype(np.float32)

            if class_index == 0:  # empty: 작은 잡음만 존재
                pattern = 0.25 * noise
            elif class_index == 1:  # still: 호흡을 흉내 낸 저주파 변화
                frequency = rng.uniform(0.8, 1.4)
                phase = rng.uniform(-np.pi, np.pi)
                temporal = 0.10 * np.sin(2.0 * np.pi * frequency * t + phase)
                pattern = temporal[:, None] * spatial_profile[None, :] + noise
            elif class_index == 2:  # moving: 진폭이 큰 다중 주파수 변화
                f1 = rng.uniform(3.0, 6.0)
                f2 = rng.uniform(7.0, 11.0)
                temporal = (
                    0.28 * np.sin(2.0 * np.pi * f1 * t + spatial_phase)
                    + 0.12 * np.sin(2.0 * np.pi * f2 * t)
                )
                pattern = temporal[:, None] * spatial_profile[None, :] + 1.5 * noise
            else:  # bed_exit: 윈도우 후반의 급격한 상태 전이
                center = rng.uniform(0.52, 0.72)
                transition = 1.0 / (1.0 + np.exp(-(t - center) / 0.025))
                burst = np.exp(-0.5 * ((t - center) / 0.055) ** 2)
                temporal = 0.32 * transition + 0.30 * burst * np.sin(16.0 * np.pi * t)
                pattern = temporal[:, None] * spatial_profile[None, :] + noise

            samples.append((baseline + pattern).astype(np.float32))
            labels.append(class_index)
            groups.append(sample_index % num_groups)

    x = np.stack(samples)
    y = np.asarray(labels, dtype=np.int64)
    group_array = np.asarray(groups, dtype=np.int64)
    permutation = rng.permutation(x.shape[0])

    return CSIDataBundle(
        x=torch.from_numpy(x[permutation]),
        y=torch.from_numpy(y[permutation]),
        groups=group_array[permutation],
        class_names=class_names,
        label_mapping={name: index for index, name in enumerate(class_names)},
    )


def make_data_split(
    bundle: CSIDataBundle,
    seed: int,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    test_fraction: float = 0.15,
) -> DataSplit:
    fractions = np.asarray(
        [train_fraction, validation_fraction, test_fraction], dtype=np.float64
    )
    if np.any(fractions <= 0) or not np.isclose(fractions.sum(), 1.0):
        raise ValueError("train/validation/test 비율은 양수이고 합이 1이어야 합니다.")

    indices = np.arange(bundle.num_samples)
    y = bundle.y.numpy()
    holdout_fraction = validation_fraction + test_fraction
    test_within_holdout = test_fraction / holdout_fraction

    if bundle.groups is not None:
        unique_groups = np.unique(bundle.groups)
        if unique_groups.size < 7:
            raise ValueError(
                "group 기반 3-way 분할에는 최소 7개의 서로 다른 group을 권장합니다."
            )
        outer = GroupShuffleSplit(
            n_splits=1,
            test_size=holdout_fraction,
            random_state=seed,
        )
        train_pos, holdout_pos = next(
            outer.split(indices, y, groups=bundle.groups)
        )
        holdout_indices = indices[holdout_pos]
        holdout_groups = bundle.groups[holdout_pos]
        inner = GroupShuffleSplit(
            n_splits=1,
            test_size=test_within_holdout,
            random_state=seed + 1,
        )
        validation_pos, test_pos = next(
            inner.split(
                holdout_indices,
                y[holdout_pos],
                groups=holdout_groups,
            )
        )
        split = DataSplit(
            train=indices[train_pos],
            validation=holdout_indices[validation_pos],
            test=holdout_indices[test_pos],
        )
        train_groups = set(bundle.groups[split.train].tolist())
        validation_groups = set(bundle.groups[split.validation].tolist())
        test_groups = set(bundle.groups[split.test].tolist())
        if (
            not train_groups.isdisjoint(validation_groups)
            or not train_groups.isdisjoint(test_groups)
            or not validation_groups.isdisjoint(test_groups)
        ):
            raise RuntimeError("group split 사이에 동일한 group이 포함되었습니다.")
    else:
        warnings.warn(
            "groups가 없어 stratified random split을 사용합니다. 실제 CSI에서는 "
            "같은 측정 회차의 인접 window가 서로 다른 split에 들어가지 않도록 "
            "subject/session/trial ID를 groups로 제공하는 것이 안전합니다.",
            stacklevel=2,
        )
        train_indices, holdout_indices = train_test_split(
            indices,
            test_size=holdout_fraction,
            random_state=seed,
            stratify=y,
        )
        validation_indices, test_indices = train_test_split(
            holdout_indices,
            test_size=test_within_holdout,
            random_state=seed + 1,
            stratify=y[holdout_indices],
        )
        split = DataSplit(
            train=np.asarray(train_indices),
            validation=np.asarray(validation_indices),
            test=np.asarray(test_indices),
        )

    _warn_if_classes_missing(bundle, split)
    return split


def _warn_if_classes_missing(bundle: CSIDataBundle, split: DataSplit) -> None:
    expected = set(range(bundle.num_classes))
    for split_name, indices in (
        ("train", split.train),
        ("validation", split.validation),
        ("test", split.test),
    ):
        present = set(bundle.y[torch.as_tensor(indices)].tolist())
        missing = sorted(expected - present)
        if missing:
            warnings.warn(
                f"{split_name} split에 class {missing}가 없습니다. group 수 또는 "
                "측정 횟수를 늘리는 것이 좋습니다.",
                stacklevel=2,
            )


def compute_normalization_stats(
    bundle: CSIDataBundle,
    train_indices: np.ndarray,
) -> NormalizationStats:
    """학습 데이터만 이용하여 subcarrier별 평균과 표준편차를 계산한다."""

    train_x = bundle.x[torch.as_tensor(train_indices, dtype=torch.long)]
    mean = train_x.mean(dim=(0, 1))
    std = train_x.std(dim=(0, 1), unbiased=False).clamp_min(1e-6)
    return NormalizationStats(mean=mean, std=std)


def make_dataloaders(
    bundle: CSIDataBundle,
    split: DataSplit,
    stats: NormalizationStats,
    batch_size: int,
    seed: int,
    num_workers: int = 0,
    pin_memory: bool = False,
) -> dict[str, DataLoader[tuple[Tensor, Tensor]]]:
    generator = torch.Generator().manual_seed(seed)
    common = {
        "batch_size": batch_size,
        "num_workers": num_workers,
        "pin_memory": pin_memory,
    }
    return {
        "train": DataLoader(
            IndexedNormalizedDataset(bundle, split.train, stats),
            shuffle=True,
            generator=generator,
            **common,
        ),
        "validation": DataLoader(
            IndexedNormalizedDataset(bundle, split.validation, stats),
            shuffle=False,
            **common,
        ),
        "test": DataLoader(
            IndexedNormalizedDataset(bundle, split.test, stats),
            shuffle=False,
            **common,
        ),
    }
