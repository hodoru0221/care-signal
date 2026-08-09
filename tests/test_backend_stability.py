import threading
import unittest
from copy import deepcopy
import sys
from types import SimpleNamespace

from backend.domain import MAX_HISTORY_PER_ROOM, MonitoringStore
try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    sys.modules["psycopg"] = SimpleNamespace()
from backend.persistence import PersistentMonitoringStore
from backend.ward import WARD_ROOM_IDS, ward_map_payload


class AtomicMemoryRepository:
    """Repository double that models the database row lock."""

    def __init__(self):
        self.snapshot = None
        self.lock = threading.Lock()
        self.devices = []

    def initialize(self):
        pass

    def load(self):
        with self.lock:
            return deepcopy(self.snapshot)

    def save_if_empty(self, snapshot):
        with self.lock:
            if self.snapshot is not None:
                return False
            self.snapshot = deepcopy(snapshot)
            return True

    def update_snapshot(self, mutator):
        with self.lock:
            snapshot, result = mutator(deepcopy(self.snapshot))
            self.snapshot = deepcopy(snapshot)
            return result

    def device_statuses(self):
        return deepcopy(self.devices)


class BackendStabilityTests(unittest.TestCase):
    def test_unknown_room_cannot_expand_snapshot(self):
        store = MonitoringStore()
        with self.assertRaises(ValueError):
            store.update_room("attacker-controlled-room", "IN_BED", 0.9)
        self.assertEqual({room["room_id"] for room in store.rooms()}, set(WARD_ROOM_IDS))

    def test_history_is_bounded_and_export_is_detached(self):
        store = MonitoringStore()
        for index in range(MAX_HISTORY_PER_ROOM + 10):
            store.update_room("room-01", "IN_BED", index / (MAX_HISTORY_PER_ROOM + 10))
        snapshot = store.export_snapshot()
        self.assertEqual(len(snapshot["history"]["room-01"]), MAX_HISTORY_PER_ROOM)
        snapshot["history"]["room-01"].clear()
        self.assertEqual(len(store.room_history("room-01", 200)), 200)

    def test_legacy_snapshot_without_history_remains_supported(self):
        store = MonitoringStore()
        store.import_snapshot(
            {
                "rooms": {
                    "room-01": {
                        "room_id": "room-01",
                        "state": "IN_BED",
                        "confidence": 0.8,
                        "risk_level": "NORMAL",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "future_field": "ignored",
                    }
                },
                "events": {},
            }
        )
        self.assertEqual(store.room("room-01")["state"], "IN_BED")
        self.assertEqual(store.room_history("room-01"), [])
        self.assertEqual(len(ward_map_payload(store)["rooms"]), len(WARD_ROOM_IDS))

    def test_two_store_instances_do_not_lose_updates(self):
        repository = AtomicMemoryRepository()
        first = PersistentMonitoringStore(repository)
        second = PersistentMonitoringStore(repository)

        def write(store, state):
            for _ in range(40):
                store.update_room("room-02", state, 0.9)

        threads = [
            threading.Thread(target=write, args=(first, "IN_BED")),
            threading.Thread(target=write, args=(second, "OUT_OF_BED")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(first.room_history("room-02", 200)), 80)

    def test_persistent_guardian_view_uses_repository_device_freshness(self):
        repository = AtomicMemoryRepository()
        store = PersistentMonitoringStore(repository)
        self.assertFalse(store.guardian_view("room-01")["sensor_online"])
        repository.devices = [
            {"room_id": "room-01", "online": True},
            {"room_id": "room-02", "online": False},
        ]
        self.assertTrue(store.guardian_view("room-01")["sensor_online"])
        self.assertFalse(store.guardian_view("room-02")["sensor_online"])


if __name__ == "__main__":
    unittest.main()
