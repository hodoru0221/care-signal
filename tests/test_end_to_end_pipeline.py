import os
import unittest
from unittest.mock import patch

os.environ["STAFF_ACCESS_CODE"] = "NURSE-101"
os.environ["DEVICE_API_KEY"] = "dev-device-key"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("NEON_DATABASE_URL", None)

from fastapi.testclient import TestClient

import backend.main as main
from backend.domain import MonitoringStore
from gateway.model_bridge import normalize


class EndToEndModelPipelineTests(unittest.TestCase):
    def setUp(self):
        main.store = MonitoringStore()
        main.STAFF_SESSIONS.clear()
        self.client = TestClient(main.app)

    @patch("backend.main.notify_guardians")
    def test_model_alias_is_recorded_once_and_visible_to_staff(self, notify):
        observation = normalize(
            {
                "label": "FALL",
                "score": 0.94,
                "timestamp": "2026-08-09T09:00:00Z",
                "sequence": 7,
            },
            room_id="room-03",
            device_id="csi-gateway-a-01",
            model_version="care-csi-1.0.0",
        )
        headers = {"X-Device-Key": "dev-device-key"}
        first = self.client.post("/api/v1/inference", headers=headers, json=observation)
        replay = self.client.post("/api/v1/inference", headers=headers, json=observation)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["state"], "MOVEMENT_ANOMALY")
        self.assertFalse(first.json()["duplicate"])
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(len(main.store.room_history("room-03")), 1)
        self.assertEqual(len(main.store.events()), 1)
        notify.assert_called_once()

        login = self.client.post(
            "/api/v1/staff/login", json={"access_code": "NURSE-101"}
        ).json()
        staff = {"Authorization": f"Bearer {login['access_token']}"}
        records = self.client.get(
            "/api/v1/observations?room_id=room-03", headers=staff
        ).json()
        devices = self.client.get("/api/v1/devices/status", headers=staff).json()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model_version"], "care-csi-1.0.0")
        self.assertIn("received_at", records[0])
        self.assertEqual(devices[0]["device_id"], "csi-gateway-a-01")
        self.assertIn("online", devices[0])


if __name__ == "__main__":
    unittest.main()
