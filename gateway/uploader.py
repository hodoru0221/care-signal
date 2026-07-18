"""Hospital-side gateway: uploads model results to the public API with retry buffering."""
import argparse
import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def upload(api_url: str, device_key: str, payload: dict) -> None:
    request = Request(
        f"{api_url.rstrip('/')}/api/v1/inference",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Device-Key": device_key},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise URLError(f"HTTP {response.status}")


def flush(api_url: str, device_key: str, spool: Path) -> None:
    if not spool.exists():
        return
    pending = [line for line in spool.read_text(encoding="utf-8").splitlines() if line]
    remaining = []
    for index, line in enumerate(pending):
        try:
            upload(api_url, device_key, json.loads(line))
        except Exception:
            remaining = pending[index:]
            break
    spool.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--device-key", required=True)
    parser.add_argument("--input", default="inference.jsonl")
    parser.add_argument("--spool", default="pending_uploads.jsonl")
    args = parser.parse_args()
    source, spool = Path(args.input), Path(args.spool)
    source.touch(exist_ok=True)
    position = 0
    while True:
        flush(args.api_url, args.device_key, spool)
        with source.open(encoding="utf-8") as stream:
            stream.seek(position)
            while True:
                line = stream.readline()
                if not line:
                    break
                position = stream.tell()
                try:
                    upload(args.api_url, args.device_key, json.loads(line))
                except Exception:
                    with spool.open("a", encoding="utf-8") as pending:
                        pending.write(line.rstrip() + "\n")
        time.sleep(2)


if __name__ == "__main__":
    main()
