import unittest
from datetime import datetime, timedelta, timezone

from backend.domain import MonitoringStore, SensorObservation
from backend.notifications import build_push_messages
from backend.ward import WARD_LAYOUT, ward_map_payload


class MonitoringStoreTests(unittest.TestCase):
    def test_risky_state_creates_one_event(self):
        store = MonitoringStore()
        store.update_room("room-01", "OUT_OF_BED", 0.9)
        store.update_room("room-01", "OUT_OF_BED", 0.91)
        self.assertEqual(len(store.events()), 1)
        self.assertEqual(store.events()[0]["risk_level"], "WARNING")

    def test_acknowledgement_stops_device_sound(self):
        store = MonitoringStore()
        store.update_room("room-01", "MOVEMENT_ANOMALY", 0.88)
        event_id = store.events()[0]["id"]
        self.assertTrue(store.device_alert("room-01", "nurse")["sound"])
        store.update_event(event_id, "ACKNOWLEDGED", "nurse-01")
        self.assertFalse(store.device_alert("room-01", "nurse")["sound"])

    def test_event_progress_cannot_move_backwards_or_reopen_after_completion(self):
        store = MonitoringStore()
        store.update_room("room-01", "MOVEMENT_ANOMALY", 0.88)
        event_id = store.events()[0]["id"]
        store.update_event(event_id, "RESPONDING", "nurse-01")
        with self.assertRaises(ValueError):
            store.update_event(event_id, "ACKNOWLEDGED", "nurse-01")
        completed = store.update_event(event_id, "COMPLETED", "nurse-01")
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(
            store.update_event(event_id, "COMPLETED", "nurse-01")["completed_at"],
            completed["completed_at"],
        )
        with self.assertRaises(ValueError):
            store.update_event(event_id, "RESPONDING", "nurse-01")

    def test_only_completed_events_can_be_deleted(self):
        store = MonitoringStore()
        store.update_room("room-01", "OUT_OF_BED", 0.9)
        active_id = store.events()[0]["id"]
        with self.assertRaises(ValueError):
            store.delete_completed_events([active_id])
        store.update_event(active_id, "COMPLETED", "nurse-01")
        result = store.delete_completed_events([active_id])
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(store.events(), [])
        with self.assertRaises(KeyError):
            store.delete_completed_events([active_id])

    def test_invalid_confidence_is_rejected(self):
        store = MonitoringStore()
        with self.assertRaises(ValueError):
            store.update_room("room-01", "IN_BED", 1.2)

    def test_snapshot_restores_room_and_events(self):
        source = MonitoringStore()
        source.update_room("room-01", "OUT_OF_BED", 0.9)
        snapshot = source.export_snapshot()
        restored = MonitoringStore()
        restored.import_snapshot(snapshot)
        self.assertEqual(restored.room("room-01")["state"], "OUT_OF_BED")
        self.assertEqual(len(restored.events()), 1)

    def test_room_history_is_persisted_in_snapshot(self):
        source = MonitoringStore()
        source.update_room("room-02", "IN_BED", 0.93)
        source.update_room("room-02", "OUT_OF_BED", 0.87)
        restored = MonitoringStore()
        restored.import_snapshot(source.export_snapshot())
        history = restored.room_history("room-02")
        self.assertEqual([item["state"] for item in history], ["OUT_OF_BED", "IN_BED"])

    def test_old_snapshot_gains_all_ward_rooms(self):
        restored = MonitoringStore()
        restored.import_snapshot({"rooms": {}, "events": {}})
        self.assertEqual(len(restored.rooms()), len(WARD_LAYOUT["rooms"]))

    def test_old_snapshot_without_history_keeps_data_and_adds_new_rooms(self):
        source = MonitoringStore()
        source.update_room("room-01", "OUT_OF_BED", 0.9)
        old_snapshot = source.export_snapshot()
        old_snapshot.pop("history")
        old_snapshot["rooms"] = {"room-01": old_snapshot["rooms"]["room-01"]}

        restored = MonitoringStore()
        restored.import_snapshot(old_snapshot)

        self.assertEqual(restored.room("room-01")["state"], "OUT_OF_BED")
        self.assertEqual(len(restored.rooms()), len(WARD_LAYOUT["rooms"]))
        self.assertEqual(len(restored.events()), 1)
        self.assertEqual(restored.room_history("room-01"), [])

    def test_room_histories_are_isolated_and_newest_first(self):
        store = MonitoringStore()
        store.update_room("room-01", "IN_BED", 0.91)
        store.update_room("room-02", "OUT_OF_BED", 0.82)
        store.update_room("room-01", "EMPTY", 0.99)
        self.assertEqual(
            [item["state"] for item in store.room_history("room-01")],
            ["EMPTY", "IN_BED"],
        )
        self.assertEqual(
            [item["state"] for item in store.room_history("room-02")],
            ["OUT_OF_BED"],
        )

    def test_ward_map_combines_layout_and_live_status(self):
        store = MonitoringStore()
        store.update_room("room-03", "MOVEMENT_ANOMALY", 0.91)
        payload = ward_map_payload(store)
        room = next(item for item in payload["rooms"] if item["room_id"] == "room-03")
        self.assertEqual(room["label"], "103호")
        self.assertEqual(room["status"]["risk_level"], "CRITICAL")

    def test_push_message_hides_model_confidence(self):
        messages = build_push_messages(["ExponentPushToken[test]"], "OUT_OF_BED", "evt-1")
        self.assertEqual(len(messages), 1)
        self.assertNotIn("confidence", messages[0])
        self.assertEqual(messages[0]["data"]["event_id"], "evt-1")

    def test_guardian_sees_response_progress_without_clinical_details(self):
        store = MonitoringStore()
        store.update_room("room-01", "MOVEMENT_ANOMALY", 0.88)
        event_id = store.events()[0]["id"]
        before = store.guardian_view("room-01")
        self.assertEqual(before["display_state"], "WARD_NOTIFIED")
        self.assertNotIn("confidence", before)
        store.update_event(event_id, "RESPONDING", "nurse-01")
        after = store.guardian_view("room-01")
        self.assertEqual(after["display_state"], "STAFF_CHECKING")
        history = store.guardian_events("room-01")
        self.assertEqual(history[0]["summary"], "병동 상태 확인")
        self.assertNotIn("confidence", history[0])

    def test_guardian_sensor_status_uses_latest_room_observation(self):
        store = MonitoringStore()
        now = datetime.now(timezone.utc)
        store.record_observation(SensorObservation(
            observation_id="stale", room_id="room-01", device_id="sensor-old",
            state="IN_BED", confidence=0.9, captured_at=(now - timedelta(minutes=5)).isoformat(),
            received_at=(now - timedelta(minutes=5)).isoformat(), model_version="v1",
        ))
        self.assertFalse(store.guardian_view("room-01")["sensor_online"])

        store.record_observation(SensorObservation(
            observation_id="fresh", room_id="room-01", device_id="sensor-live",
            state="IN_BED", confidence=0.95, captured_at=now.isoformat(),
            received_at=now.isoformat(), model_version="v1",
        ))
        self.assertTrue(store.guardian_view("room-01")["sensor_online"])
        self.assertFalse(store.guardian_view("room-02")["sensor_online"])


if __name__ == "__main__":
    unittest.main()
