import json
from threading import RLock
from typing import Optional

import psycopg

from backend.domain import MonitoringStore


class PostgresSnapshotRepository:
    """Stores the prototype's monitoring state as one atomic JSONB snapshot."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def initialize(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monitoring_snapshot (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    token TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    def load(self) -> Optional[dict]:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT payload FROM monitoring_snapshot WHERE id = 1"
            ).fetchone()
            return row[0] if row else None

    def save(self, snapshot: dict) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO monitoring_snapshot (id, payload, updated_at)
                VALUES (1, %s::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (payload,),
            )

    def save_if_empty(self, snapshot: dict) -> bool:
        payload = json.dumps(snapshot, ensure_ascii=False)
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                INSERT INTO monitoring_snapshot (id, payload, updated_at)
                VALUES (1, %s::jsonb, NOW())
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                (payload,),
            ).fetchone()
            return row is not None

    def update_snapshot(self, mutator):
        """Serialize a read-modify-write cycle across processes and instances."""
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT payload FROM monitoring_snapshot WHERE id = 1 FOR UPDATE"
            ).fetchone()
            if row is None:
                raise RuntimeError("monitoring snapshot is not initialized")
            snapshot, result = mutator(row[0])
            payload = json.dumps(snapshot, ensure_ascii=False)
            connection.execute(
                "UPDATE monitoring_snapshot SET payload = %s::jsonb, updated_at = NOW() WHERE id = 1",
                (payload,),
            )
            return result

    def register_push_token(self, patient_id: str, token: str) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO push_subscriptions (token, patient_id, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (token) DO UPDATE
                SET patient_id = EXCLUDED.patient_id, updated_at = NOW()
                """,
                (token, patient_id),
            )

    def push_tokens(self, patient_id: str) -> list[str]:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT token FROM push_subscriptions WHERE patient_id = %s",
                (patient_id,),
            ).fetchall()
            return [row[0] for row in rows]

    def all_push_subscriptions(self) -> list[tuple[str, str]]:
        """Return patient/token pairs for a one-time database migration."""
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT patient_id, token FROM push_subscriptions"
            ).fetchall()
            return [(row[0], row[1]) for row in rows]


def migrate_repository_if_empty(
    target: PostgresSnapshotRepository,
    source: PostgresSnapshotRepository,
) -> bool:
    """Copy legacy data once, without overwriting an initialized target."""
    target.initialize()
    source.initialize()
    snapshot = source.load()
    if snapshot is None:
        return False

    if not target.save_if_empty(snapshot):
        return False
    for patient_id, token in source.all_push_subscriptions():
        target.register_push_token(patient_id, token)
    return True


class PersistentMonitoringStore:
    def __init__(self, repository: PostgresSnapshotRepository) -> None:
        self._lock = RLock()
        self.memory = MonitoringStore()
        self.repository = repository
        self.repository.initialize()
        self.repository.save_if_empty(self.memory.export_snapshot())
        self.memory.import_snapshot(self.repository.load())

    def _mutate(self, method_name: str, *args):
        with self._lock:
            def mutate(snapshot):
                latest = MonitoringStore()
                latest.import_snapshot(snapshot)
                result = getattr(latest, method_name)(*args)
                updated = latest.export_snapshot()
                self.memory.import_snapshot(updated)
                return updated, result

            return self.repository.update_snapshot(mutate)

    def _read(self, method_name: str, *args):
        with self._lock:
            snapshot = self.repository.load()
            if snapshot is not None:
                self.memory.import_snapshot(snapshot)
            return getattr(self.memory, method_name)(*args)

    def update_room(self, room_id: str, state: str, confidence: float) -> dict:
        return self._mutate("update_room", room_id, state, confidence)

    def update_event(self, event_id: str, status: str, actor: str) -> dict:
        return self._mutate("update_event", event_id, status, actor)

    def room(self, room_id: str) -> dict:
        return self._read("room", room_id)

    def rooms(self) -> list[dict]:
        return self._read("rooms")

    def room_history(self, room_id: str, limit: int = 50) -> list[dict]:
        return self._read("room_history", room_id, limit)

    def events(self) -> list[dict]:
        return self._read("events")

    def device_alert(self, room_id: str, location: str) -> dict:
        return self._read("device_alert", room_id, location)

    def guardian_view(self, room_id: str) -> dict:
        return self._read("guardian_view", room_id)

    def guardian_events(self, room_id: str) -> list[dict]:
        return self._read("guardian_events", room_id)

