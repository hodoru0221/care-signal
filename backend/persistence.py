import json
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


class PersistentMonitoringStore:
    def __init__(self, repository: PostgresSnapshotRepository) -> None:
        self.memory = MonitoringStore()
        self.repository = repository
        self.repository.initialize()
        snapshot = self.repository.load()
        if snapshot:
            self.memory.import_snapshot(snapshot)
        else:
            self.repository.save(self.memory.export_snapshot())

    def update_room(self, room_id: str, state: str, confidence: float) -> dict:
        result = self.memory.update_room(room_id, state, confidence)
        self.repository.save(self.memory.export_snapshot())
        return result

    def update_event(self, event_id: str, status: str, actor: str) -> dict:
        result = self.memory.update_event(event_id, status, actor)
        self.repository.save(self.memory.export_snapshot())
        return result

    def room(self, room_id: str) -> dict:
        return self.memory.room(room_id)

    def events(self) -> list[dict]:
        return self.memory.events()

    def device_alert(self, room_id: str, location: str) -> dict:
        return self.memory.device_alert(room_id, location)

    def guardian_view(self, room_id: str) -> dict:
        return self.memory.guardian_view(room_id)

    def guardian_events(self, room_id: str) -> list[dict]:
        return self.memory.guardian_events(room_id)
