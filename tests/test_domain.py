import unittest

from backend.domain import MonitoringStore
from backend.notifications import build_push_messages


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


if __name__ == "__main__":
    unittest.main()
