"""Generate a reproducible synthetic CSI window file for integration demos."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from model.csi_project.data import make_synthetic_csi


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="model/outputs/demo/csi_windows.npz")
    parser.add_argument("--samples-per-class", type=int, default=20)
    parser.add_argument("--sequence-length", type=int, default=128)
    parser.add_argument("--num-subcarriers", type=int, default=30)
    parser.add_argument("--num-groups", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    bundle = make_synthetic_csi(
        samples_per_class=args.samples_per_class,
        sequence_length=args.sequence_length,
        num_subcarriers=args.num_subcarriers,
        num_groups=args.num_groups,
        seed=args.seed,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output,
        X=bundle.x.numpy(),
        y=bundle.y.numpy(),
        groups=bundle.groups,
        class_names=np.asarray(bundle.class_names),
    )
    print(f"generated={bundle.num_samples} shape={tuple(bundle.x.shape)} output={output.resolve()}")


if __name__ == "__main__":
    main()
