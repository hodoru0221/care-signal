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

    def test_health_reports_storage_and_deployment_revision(self):
        payload = self.client.get("/health").json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "care-signal-api")
        self.assertIn(payload["storage"], {"memory", "render-postgres", "neon-postgres"})
        self.assertTrue(payload["revision"])

    def test_public_pages_do_not_embed_demo_credentials(self):
        dashboard = self.client.get("/").text
        guardian = self.client.get("/guardian").text
        simulator = self.client.get("/simulator").text
        self.assertNotIn('value="NURSE-101"', dashboard)
        self.assertNotIn("시연 코드: CARE-101", guardian)
        self.assertIn("테스트 데이터 전송기", simulator)
        self.assertNotIn('value="NURSE-101"', simulator)

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

    def test_guardian_logout_revokes_session(self):
        login = self.client.post(
            "/api/v1/guardian/login", json={"connection_code": "care-101"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(
            self.client.post("/api/v1/guardian/logout", headers=headers).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/guardian/patient", headers=headers).status_code,
            401,
        )

    def test_staff_logout_revokes_session(self):
        headers = self.staff_headers()
        self.assertEqual(
            self.client.post("/api/v1/staff/logout", headers=headers).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/ward/map", headers=headers).status_code,
            401,
        )

    def test_database_sessions_survive_memory_reset_and_store_only_hashes(self):
        class SessionRepository:
            def __init__(self):
                self.guardian = set()
                self.staff = set()

            def register_guardian_session(self, token_hash, _patient_id, _expires_at):
                self.guardian.add(token_hash)

            def guardian_session_exists(self, token_hash):
                return token_hash in self.guardian

            def revoke_guardian_session(self, token_hash):
                self.guardian.discard(token_hash)

            def register_staff_session(self, token_hash, _role, _expires_at):
                self.staff.add(token_hash)

            def staff_session_exists(self, token_hash):
                return token_hash in self.staff

            def revoke_staff_session(self, token_hash):
                self.staff.discard(token_hash)

        repository = SessionRepository()
        with patch.object(main, "postgres_repository", repository):
            guardian_login = self.client.post(
                "/api/v1/guardian/login", json={"connection_code": "care-101"}
            )
            staff_login = self.client.post(
                "/api/v1/staff/login", json={"access_code": "nurse-101"}
            )
            guardian_token = guardian_login.json()["access_token"]
            staff_token = staff_login.json()["access_token"]
            main.GUARDIAN_SESSIONS.clear()
            main.STAFF_SESSIONS.clear()

            self.assertEqual(
                self.client.get(
                    "/api/v1/guardian/patient",
                    headers={"Authorization": f"Bearer {guardian_token}"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    "/api/v1/ward/map",
                    headers={"Authorization": f"Bearer {staff_token}"},
                ).status_code,
                200,
            )
            self.assertNotIn(guardian_token, repository.guardian)
            self.assertNotIn(staff_token, repository.staff)
            self.assertTrue(all(len(value) == 64 for value in repository.guardian | repository.staff))

    def test_room_status_and_alert_device_are_not_public(self):
        self.assertEqual(
            self.client.get("/api/v1/rooms/room-01/status").status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/rooms/room-01/status", headers=self.staff_headers()
            ).status_code,
            200,
        )
        main.store.update_room("room-01", "OUT_OF_BED", 0.9)
        url = "/api/v1/devices/uno-room-01/alert?room_id=room-01&location=room"
        self.assertEqual(self.client.get(url).status_code, 401)
        response = self.client.get(url, headers={"X-Device-Key": "dev-device-key"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sound"])
        self.assertEqual(
            self.client.get(
                "/api/v1/devices/uno-room-99/alert?room_id=room-99&location=room",
                headers={"X-Device-Key": "dev-device-key"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/devices/uno-room-01/alert?room_id=room-01&location=unknown",
                headers={"X-Device-Key": "dev-device-key"},
            ).status_code,
            422,
        )

    def test_event_api_rejects_progress_regression(self):
        headers = self.staff_headers()
        main.store.update_room("room-01", "OUT_OF_BED", 0.9)
        event_id = main.store.events()[0]["id"]
        responding = self.client.patch(
            f"/api/v1/events/{event_id}",
            headers=headers,
            json={"status": "RESPONDING", "actor": "nurse-01"},
        )
        self.assertEqual(responding.status_code, 200)
        regression = self.client.patch(
            f"/api/v1/events/{event_id}",
            headers=headers,
            json={"status": "ACKNOWLEDGED", "actor": "nurse-01"},
        )
        self.assertEqual(regression.status_code, 400)

    def test_demo_cleanup_deletes_only_selected_completed_events(self):
        headers = self.staff_headers()
        main.store.update_room("room-01", "OUT_OF_BED", 0.9)
        first_id = main.store.events()[0]["id"]
        main.store.update_event(first_id, "COMPLETED", "nurse-01")
        main.store.update_room("room-01", "IN_BED", 0.99)
        main.store.update_room("room-01", "MOVEMENT_ANOMALY", 0.91)
        active_id = next(
            event["id"] for event in main.store.events() if event["status"] == "OPEN"
        )

        deleted = self.client.post(
            "/api/v1/demo/events/cleanup",
            headers=headers,
            json={"event_ids": [first_id], "confirmation": "DELETE"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_event_ids"], [first_id])
        self.assertEqual([event["id"] for event in main.store.events()], [active_id])

        active = self.client.post(
            "/api/v1/demo/events/cleanup",
            headers=headers,
            json={"event_ids": [active_id], "confirmation": "DELETE"},
        )
        self.assertEqual(active.status_code, 400)
        wrong_confirmation = self.client.post(
            "/api/v1/demo/events/cleanup",
            headers=headers,
            json={"event_ids": [active_id], "confirmation": "WRONG"},
        )
        self.assertEqual(wrong_confirmation.status_code, 422)

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

    def test_demo_state_uses_the_observation_pipeline(self):
        headers = self.staff_headers()
        response = self.client.post(
            "/api/v1/demo/state",
            headers=headers,
            json={"room_id": "room-05", "state": "IN_BED", "confidence": 0.92},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["duplicate"])
        records = self.client.get(
            "/api/v1/observations?room_id=room-05", headers=headers
        ).json()
        self.assertEqual(records[0]["device_id"], "demo-console")
        self.assertEqual(records[0]["model_version"], "demo-panel")

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
            422,
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

    def test_inference_rejects_room_outside_configured_ward(self):
        response = self.client.post(
            "/api/v1/inference",
            headers={"X-Device-Key": "dev-device-key"},
            json={"room_id": "room-99", "state": "IN_BED", "confidence": 0.9},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
