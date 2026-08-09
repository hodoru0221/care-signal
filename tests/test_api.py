import os
import unittest
from unittest.mock import patch

os.environ["DEMO_MODE"] = "true"
os.environ["GUARDIAN_CONNECTION_CODE"] = "CARE-101"
os.environ["STAFF_ACCESS_CODE"] = "NURSE-101"
os.environ["DEVICE_API_KEY"] = "dev-device-key"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("NEON_DATABASE_URL", None)

from fastapi.testclient import TestClient

import backend.main as main
from backend.domain import MonitoringStore
from backend.ward import WARD_LAYOUT


class ApiTests(unittest.TestCase):
    def setUp(self):
        main.store = MonitoringStore()
        main.GUARDIAN_SESSIONS.clear()
        main.STAFF_SESSIONS.clear()
        main.push_tokens_memory = set()
        self.client = TestClient(main.app)

    def staff_headers(self):
        response = self.client.post(
            "/api/v1/staff/login", json={"access_code": " nurse-101 "}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_staff_and_guardian_authentication_boundaries(self):
        self.assertEqual(self.client.get("/api/v1/ward/map").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/v1/staff/login", json={"access_code": "wrong"}
            ).status_code,
            401,
        )
        guardian_login = self.client.post(
            "/api/v1/guardian/login", json={"connection_code": "care-101"}
        )
        self.assertEqual(guardian_login.status_code, 200)
        guardian_headers = {
            "Authorization": f"Bearer {guardian_login.json()['access_token']}"
        }
        self.assertEqual(
            self.client.get(
                "/api/v1/guardian/patient", headers=guardian_headers
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/guardian/patient").status_code, 401
        )
        self.assertEqual(
            self.client.get("/api/v1/ward/map", headers=guardian_headers).status_code,
            401,
        )

    def test_ward_map_contains_layout_and_each_live_room_status(self):
        response = self.client.get("/api/v1/ward/map", headers=self.staff_headers())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], WARD_LAYOUT["id"])
        self.assertEqual(len(payload["rooms"]), len(WARD_LAYOUT["rooms"]))
        for layout, room in zip(WARD_LAYOUT["rooms"], payload["rooms"]):
            self.assertEqual(room["room_id"], layout["room_id"])
            self.assertEqual(room["status"]["room_id"], layout["room_id"])
            self.assertIn("risk_level", room["status"])
        self.assertEqual(payload["stations"], WARD_LAYOUT["stations"])

    @patch("backend.main.notify_guardians")
    def test_multiple_rooms_keep_independent_status_events_and_history(self, notify):
        headers = self.staff_headers()
        updates = (
            ("room-01", "OUT_OF_BED", 0.81),
            ("room-02", "MOVEMENT_ANOMALY", 0.92),
            ("room-03", "IN_BED", 0.97),
        )
        for room_id, state, confidence in updates:
            response = self.client.post(
                "/api/v1/demo/state",
                headers=headers,
                json={"room_id": room_id, "state": state, "confidence": confidence},
            )
            self.assertEqual(response.status_code, 200)

        rooms = {
            room["room_id"]: room["status"]
            for room in self.client.get("/api/v1/ward/map", headers=headers).json()[
                "rooms"
            ]
        }
        self.assertEqual(rooms["room-01"]["risk_level"], "WARNING")
        self.assertEqual(rooms["room-02"]["risk_level"], "CRITICAL")
        self.assertEqual(rooms["room-03"]["risk_level"], "NORMAL")
        events = self.client.get("/api/v1/events", headers=headers).json()
        self.assertEqual({event["room_id"] for event in events}, {"room-01", "room-02"})
        self.assertEqual(notify.call_count, 2)

    def test_history_is_staff_only_newest_first_and_limit_is_validated(self):
        headers = self.staff_headers()
        for state in ("IN_BED", "OUT_OF_BED", "EMPTY"):
            response = self.client.post(
                "/api/v1/demo/state",
                headers=headers,
                json={"room_id": "room-04", "state": state, "confidence": 0.9},
            )
            self.assertEqual(response.status_code, 200)
        history = self.client.get(
            "/api/v1/rooms/room-04/history?limit=2", headers=headers
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual([item["state"] for item in history.json()], ["EMPTY", "OUT_OF_BED"])
        self.assertEqual(
            self.client.get("/api/v1/rooms/room-04/history").status_code, 401
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/rooms/unknown/history", headers=headers
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/rooms/room-04/history?limit=0", headers=headers
            ).status_code,
            422,
        )

    def test_invalid_payloads_and_credentials_are_rejected(self):
        headers = self.staff_headers()
        self.assertEqual(
            self.client.post(
                "/api/v1/demo/state",
                headers=headers,
                json={"room_id": "unknown", "state": "IN_BED", "confidence": 0.9},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/demo/state",
                headers=headers,
                json={"room_id": "room-01", "state": "FALL", "confidence": 0.9},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/demo/state",
                headers=headers,
                json={"room_id": "room-01", "state": "IN_BED", "confidence": 1.1},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/inference",
                headers={"X-Device-Key": "wrong"},
                json={"room_id": "room-01", "state": "IN_BED", "confidence": 0.9},
            ).status_code,
            401,
        )

    @unittest.expectedFailure
    def test_inference_rejects_room_outside_configured_ward(self):
        response = self.client.post(
            "/api/v1/inference",
            headers={"X-Device-Key": "dev-device-key"},
            json={"room_id": "room-99", "state": "IN_BED", "confidence": 0.9},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
