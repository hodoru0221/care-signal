

from __future__ import annotations

import torch
from torch import nn

from model.csi_project.data import make_data_split, make_synthetic_csi
from model.csi_project.models import MODEL_NAMES, build_model, count_trainable_parameters


def main() -> None:
    torch.manual_seed(7)
    batch_size, time_steps, subcarriers, classes = 4, 64, 30, 4
    x = torch.randn(batch_size, time_steps, subcarriers)
    y = torch.tensor([0, 1, 2, 3])
    criterion = nn.CrossEntropyLoss()

    for model_name in MODEL_NAMES:
        model = build_model(model_name, subcarriers, classes)
        if model_name == "cnn":
            logits = model(x)
        else:
            logits, attention = model(x, return_attention=True)
            assert attention.shape[0] == batch_size
            assert torch.allclose(
                attention.sum(dim=1),
                torch.ones(batch_size),
                atol=1e-5,
            )
        assert logits.shape == (batch_size, classes)
        loss = criterion(logits, y)
        loss.backward()
        print(
            f"PASS {model_name:24s} "
            f"output={tuple(logits.shape)} "
            f"params={count_trainable_parameters(model):,}"
        )

    bundle = make_synthetic_csi(samples_per_class=20, num_groups=10, seed=7)
    split = make_data_split(bundle, seed=7)
    train_groups = set(bundle.groups[split.train].tolist())
    validation_groups = set(bundle.groups[split.validation].tolist())
    test_groups = set(bundle.groups[split.test].tolist())
    assert train_groups.isdisjoint(validation_groups)
    assert train_groups.isdisjoint(test_groups)
    assert validation_groups.isdisjoint(test_groups)
    print("PASS group-based split has no group overlap")


if __name__ == "__main__":
    main()
