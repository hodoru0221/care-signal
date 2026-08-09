import os
import unittest
from unittest.mock import patch

os.environ["STAFF_ACCESS_CODE"] = "NURSE-101"
os.environ["DEVICE_API_KEY"] = "dev-device-key"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("NEON_DATABASE_URL", None)

from fastapi.testclient import TestClient

import backend.main as main
from backend.domain import MAX_OBSERVATIONS, MonitoringStore


class ObservationPersistenceTests(unittest.TestCase):
    def setUp(self):
        main.store = MonitoringStore()
        main.STAFF_SESSIONS.clear()
        self.client = TestClient(main.app)

    @property
    def device_headers(self):
        return {"X-Device-Key": "dev-device-key"}

    def staff_headers(self):
        response = self.client.post(
            "/api/v1/staff/login", json={"access_code": "NURSE-101"}
        )
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    @patch("backend.main.notify_guardians")
    def test_full_observation_is_queryable_and_duplicate_is_side_effect_free(self, notify):
        payload = {
            "observation_id": "obs-001",
            "room_id": "room-01",
            "device_id": "sensor-7",
            "state": "OUT_OF_BED",
            "confidence": 0.91,
            "captured_at": "2026-08-09T01:02:03Z",
            "model_version": "care-signal-2.1",
            "sequence_no": 42,
        }
        first = self.client.post("/api/v1/inference", headers=self.device_headers, json=payload)
        second = self.client.post("/api/v1/inference", headers=self.device_headers, json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["duplicate"])
        self.assertEqual(first.json()["observation_id"], "obs-001")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(len(main.store.room_history("room-01")), 1)
        self.assertEqual(len(main.store.events()), 1)
        notify.assert_called_once()

        headers = self.staff_headers()
        observations = self.client.get(
            "/api/v1/observations?room_id=room-01&device_id=sensor-7&limit=1",
            headers=headers,
        )
        self.assertEqual(observations.status_code, 200)
        self.assertEqual(observations.json()[0]["sequence_no"], 42)
        self.assertIn("received_at", observations.json()[0])
        devices = self.client.get("/api/v1/devices/status", headers=headers)
        self.assertEqual(devices.json()[0]["device_id"], "sensor-7")
        self.assertTrue(devices.json()[0]["online"])
        self.assertIn("last_received_at", devices.json()[0])

    def test_legacy_minimal_payload_remains_supported(self):
        response = self.client.post(
            "/api/v1/inference",
            headers=self.device_headers,
            json={"room_id": "room-02", "state": "IN_BED", "confidence": 0.8},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("observation_id", response.json())
        self.assertFalse(response.json()["duplicate"])
        self.assertEqual(response.json()["room_id"], "room-02")

    def test_invalid_room_timestamp_lengths_nan_and_query_auth_are_rejected(self):
        base = {
            "observation_id": "obs-invalid",
            "room_id": "room-01",
            "device_id": "sensor-1",
            "state": "IN_BED",
            "confidence": 0.9,
            "captured_at": "2026-08-09T01:02:03Z",
            "model_version": "v1",
        }
        cases = [
            {**base, "room_id": "room-99"},
            {**base, "captured_at": "2026-08-09T01:02:03"},
            {**base, "captured_at": "2026-08-09T10:02:03+09:00"},
            {**base, "device_id": "x" * 129},
            {**base, "model_version": "x" * 129},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/v1/inference", headers=self.device_headers, json=payload
                )
                self.assertIn(response.status_code, (400, 422))
        nan_response = self.client.post(
            "/api/v1/inference", headers={**self.device_headers, "Content-Type": "application/json"},
            content=(
                '{"observation_id":"obs-nan","room_id":"room-01",'
                '"device_id":"sensor-1","state":"IN_BED","confidence":NaN,'
                '"captured_at":"2026-08-09T01:02:03Z","model_version":"v1"}'
            ),
        )
        self.assertEqual(nan_response.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/observations").status_code, 401)
        headers = self.staff_headers()
        self.assertEqual(
            self.client.get("/api/v1/observations?room_id=room-99", headers=headers).status_code,
            400,
        )
        self.assertEqual(
            self.client.get("/api/v1/observations?limit=501", headers=headers).status_code,
            422,
        )

    def test_memory_observations_are_bounded(self):
        store = MonitoringStore()
        from backend.domain import SensorObservation

        for index in range(MAX_OBSERVATIONS + 1):
            store.record_observation(SensorObservation(
                observation_id=f"obs-{index}", room_id="room-01", device_id="device-1",
                state="IN_BED", confidence=0.9,
                captured_at=f"2026-08-09T01:00:{index % 60:02d}+00:00",
                model_version="v1", sequence_no=index,
            ))
        self.assertEqual(len(store.observations(limit=MAX_OBSERVATIONS + 10)), MAX_OBSERVATIONS)
        self.assertEqual(store.observations(limit=MAX_OBSERVATIONS + 10)[-1]["observation_id"], "obs-1")


if __name__ == "__main__":
    unittest.main()
