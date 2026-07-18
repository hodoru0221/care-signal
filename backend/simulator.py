import argparse
import json
import time
from urllib.request import Request, urlopen


SEQUENCE = [
    ("EMPTY", 0.98, 4),
    ("IN_BED", 0.94, 8),
    ("OUT_OF_BED", 0.88, 8),
    ("IN_BED", 0.92, 6),
    ("MOVEMENT_ANOMALY", 0.86, 8),
]


def send(base_url: str, state: str, confidence: float, device_key: str) -> None:
    body = json.dumps(
        {"room_id": "room-01", "state": state, "confidence": confidence}
    ).encode()
    request = Request(
        f"{base_url}/api/v1/inference",
        data=body,
        headers={"Content-Type": "application/json", "X-Device-Key": device_key},
        method="POST",
    )
    with urlopen(request) as response:
        print(response.read().decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--device-key", default="dev-device-key")
    args = parser.parse_args()
    while True:
        for state, confidence, duration in SEQUENCE:
            send(args.url, state, confidence, args.device_key)
            time.sleep(duration)
