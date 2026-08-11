"""Hospital gateway uploader with durable, de-duplicated retry buffering."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from gateway.model_bridge import observation_id


def ensure_observation_id(payload: dict) -> dict:
    payload = dict(payload)
    payload["observation_id"] = str(payload.get("observation_id") or observation_id(payload))
    return payload


def upload(api_url: str, device_key: str, payload: dict) -> None:
    request = Request(f"{api_url.rstrip('/')}/api/v1/inference",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "X-Device-Key": device_key},
                      method="POST")
    with urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise URLError(f"HTTP {response.status}")


def _atomic_lines(path: Path, lines: list[str]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + ("\n" if lines else ""))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def enqueue(spool: Path, payload: dict) -> bool:
    payload = ensure_observation_id(payload)
    existing = set()
    if spool.exists():
        for line in spool.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("observation_id"))
            except json.JSONDecodeError:
                pass
    if payload["observation_id"] in existing:
        return False
    with spool.open("a", encoding="utf-8") as pending:
        pending.write(json.dumps(payload, separators=(",", ":")) + "\n")
        pending.flush()
        os.fsync(pending.fileno())
    return True


def flush(api_url: str, device_key: str, spool: Path, dead_letter: Path | None = None,
          send: Callable[[str, str, dict], None] = upload) -> int:
    if not spool.exists():
        return 0
    remaining, sent, seen = [], 0, set()
    blocked = False
    dead_letter = dead_letter or spool.with_suffix(spool.suffix + ".dead")
    for line in spool.read_text(encoding="utf-8").splitlines():
        try:
            payload = ensure_observation_id(json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            with dead_letter.open("a", encoding="utf-8") as dead:
                dead.write(json.dumps({"line": line, "error": str(exc)}) + "\n")
            continue
        identifier = payload["observation_id"]
        if identifier in seen:
            continue
        seen.add(identifier)
        if blocked:
            remaining.append(json.dumps(payload, separators=(",", ":")))
            continue
        try:
            send(api_url, device_key, payload)
            sent += 1
        except Exception:  # network errors are retried; secrets are never logged
            blocked = True
            remaining.append(json.dumps(payload, separators=(",", ":")))
    _atomic_lines(spool, remaining)
    return sent


class JsonlTail:
    """Read complete lines and recover when a producer truncates or rotates a file."""
    def __init__(self, path: Path):
        self.path, self.position, self.identity = path, 0, None

    def read(self) -> list[str]:
        self.path.touch(exist_ok=True)
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.position:
            self.position = 0
        self.identity = identity
        lines = []
        with self.path.open(encoding="utf-8") as stream:
            stream.seek(self.position)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith("\n"):
                    self.position = start
                    break
                self.position = stream.tell()
                lines.append(line.rstrip("\r\n"))
        return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--device-key", required=True)
    parser.add_argument("--input", default="inference.jsonl")
    parser.add_argument("--spool", default="pending_uploads.jsonl")
    parser.add_argument("--dead-letter", default="gateway.dead.jsonl")
    args = parser.parse_args()
    spool, dead, tail = Path(args.spool), Path(args.dead_letter), JsonlTail(Path(args.input))
    while True:
        flush(args.api_url, args.device_key, spool, dead)
        for line in tail.read():
            try:
                payload = ensure_observation_id(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                with dead.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"line": line, "error": str(exc)}) + "\n")
                continue
            try:
                upload(args.api_url, args.device_key, payload)
            except Exception:
                enqueue(spool, payload)
        time.sleep(2)


if __name__ == "__main__":
    main()
