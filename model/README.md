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
