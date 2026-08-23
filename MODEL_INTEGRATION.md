# Care Signal model integration

The hospital-side model and gateway exchange newline-delimited JSON. The normalized observation contract is:

The received CSI comparison project is integrated under `model/`. See `model/README.md` for training a checkpoint and converting `.npz`/`.mat` CSI windows directly into the gateway's `inference.jsonl` contract.

| Field | Requirement |
| --- | --- |
| `observation_id` | Unique string. The bridge/gateway derives a deterministic SHA-256-based ID when absent. |
| `room_id`, `device_id` | Non-empty deployment identifiers. |
| `state` | `EMPTY`, `IN_BED`, `OUT_OF_BED`, or `MOVEMENT_ANOMALY`. |
| `confidence` | Number from 0 through 1. |
| `captured_at` | UTC ISO 8601 timestamp (`...Z`). Naive timestamps are rejected. |
| `model_version` | Non-empty deployed model version. |
| `sequence_no` | Optional integer assigned by the producing device/process. |

The bridge accepts `state`, `label`, or `class`; `confidence` or `score`; and `captured_at` or `timestamp`. It maps common labels including `VACANT`, `LYING`, `OOB`, `FALL`, and `ANOMALY` to the standard states. Unknown labels and malformed records are quarantined rather than blocking later observations.

## Run the bridge

```powershell
python -m gateway.model_bridge --input model-output.jsonl --output inference.jsonl --dead-letter model_bridge.dead.jsonl --room-id room-01 --device-id radar-01 --model-version posture-v3
```

Values in each model record override CLI defaults. The output file is append-only, so a long-running model may invoke the reusable `normalize`/`bridge_lines` functions or run the CLI over each completed input segment. Do not place credentials in model output.

## Run the uploader

```powershell
python -m gateway.uploader --api-url https://care-signal.example --device-key $env:CARE_SIGNAL_DEVICE_KEY --input inference.jsonl --spool pending_uploads.jsonl --dead-letter gateway.dead.jsonl
```

The uploader reads only newline-terminated records, detects truncation and file replacement, and resumes at byte position zero for a new generation. Failed observations are fsync'd to a local spool. The spool is de-duplicated by `observation_id`, sent in order, and atomically rewritten after acknowledgements. Malformed input/spool lines go to the dead-letter file. Keep a single uploader process per spool; the files are not a multi-process queue.

HTTP timeouts and failures never print the device key. Protect the key through an environment variable or service secret manager, restrict permissions on JSONL files, monitor dead-letter growth, and rotate dead-letter/spool files only while the uploader is stopped. The server stores `observation_id` as a PostgreSQL primary key, so a replay returns `duplicate: true` without adding another room-history item or event. The gateway also guarantees local spool idempotency and stable IDs.
