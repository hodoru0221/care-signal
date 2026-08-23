# CSI model connection

This directory vendors the received synthetic CSI model comparison project and connects its trained checkpoints to the existing Care Signal gateway.

The four classifier labels are mapped as follows:

| CSI model | Care Signal |
| --- | --- |
| `empty` | `EMPTY` |
| `still` | `IN_BED` |
| `moving` | `MOVEMENT_ANOMALY` |
| `bed_exit` | `OUT_OF_BED` |

## Install and train

```powershell
pip install -r requirements-model.txt
python -m model.train_compare --data synthetic --epochs 10 --seeds 42 --output-dir model/outputs/synthetic
```

Synthetic scores validate code paths only. They do not measure real CSI performance.

Create reusable synthetic windows and render a signal dashboard:

```powershell
python -m model.generate_synthetic_demo --output model/outputs/demo/csi_windows.npz
python -m model.visualize_csi `
  --input model/outputs/demo/csi_windows.npz `
  --checkpoint model/outputs/synthetic/checkpoints/cnn_seed_42.pt `
  --output model/outputs/demo/csi_visualization.png
```

The visualization contains a time/subcarrier amplitude heatmap, mean amplitude trace and model probability bars. The same command accepts real `.npz` or MATLAB `-v7` `.mat` windows later.

## Convert model predictions to gateway input

The input `.npz` or MATLAB `-v7` `.mat` file must contain CSI amplitude windows named `X`. Its default shape is `[N, T, S]`.

```powershell
python -m model.infer_to_gateway `
  --checkpoint model/outputs/synthetic/checkpoints/cnn_seed_42.pt `
  --input csi_windows.npz `
  --room-id room-01 `
  --device-id csi-receiver-01 `
  --output inference.jsonl
```

The generated `inference.jsonl` already follows the Care Signal observation contract. Upload it with the existing resilient gateway:

```powershell
python -m gateway.uploader `
  --api-url https://care-signal.onrender.com `
  --device-key $env:CARE_SIGNAL_DEVICE_KEY `
  --input inference.jsonl `
  --spool pending_uploads.jsonl `
  --dead-letter gateway.dead.jsonl
```

Raw serial capture and live visualization are intentionally outside this connection step. A future collector only needs to produce the same `[N, T, S]` amplitude windows.

## Hardware-free validation completed

The following path can be verified before an ESP32 is available:

1. Run `python -m model.smoke_test` to check all three networks, attention weights, backpropagation and group isolation.
2. Train the three candidates with `python -m model.train_compare ...` and choose a checkpoint by validation Macro-F1.
3. Generate or load CSI windows, then run `model.infer_to_gateway` to produce normalized observations.
4. Run `gateway.uploader` and confirm the room state and generated event in the staff dashboard.

Local integration verification on 2026-08-24 used 160 synthetic windows and four CPU epochs. All three models trained, an independent 80-window set was inferred, the uploader delivered an observation to a local FastAPI server, and the staff API reported the matching room state and active event. These values prove the software path only; do not report the synthetic accuracy as sensor performance.
