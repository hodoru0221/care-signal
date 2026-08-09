import json
from threading import RLock
from datetime import datetime, timezone
from typing import Optional

import psycopg

from backend.domain import DEVICE_ONLINE_SECONDS, MonitoringStore, SensorObservation


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
                CREATE TABLE IF NOT EXISTS sensor_observations (
                    observation_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL,
                    model_version TEXT NOT NULL,
                    sequence_no BIGINT,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sensor_observations_room_captured_idx "
                "ON sensor_observations (room_id, captured_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sensor_observations_device_captured_idx "
                "ON sensor_observations (device_id, captured_at DESC)"
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guardian_sessions (
                    token_hash TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS guardian_sessions_expiry_idx "
                "ON guardian_sessions (expires_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS staff_sessions (
                    token_hash TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS staff_sessions_expiry_idx "
                "ON staff_sessions (expires_at)"
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

    def record_observation(self, observation: SensorObservation, mutator):
        """Append an observation and update the snapshot in one transaction."""
        with psycopg.connect(self.database_url) as connection:
            inserted = connection.execute(
                """
                INSERT INTO sensor_observations
                    (observation_id, room_id, device_id, state, confidence,
                     captured_at, model_version, sequence_no, received_at)
                VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s, %s, %s::timestamptz)
                ON CONFLICT (observation_id) DO NOTHING
                RETURNING observation_id
                """,
                (observation.observation_id, observation.room_id, observation.device_id,
                 observation.state, observation.confidence, observation.captured_at,
                 observation.model_version, observation.sequence_no, observation.received_at),
            ).fetchone()
            if inserted is None:
                row = connection.execute(
                    "SELECT payload FROM monitoring_snapshot WHERE id = 1"
                ).fetchone()
                return row[0], True
            row = connection.execute(
                "SELECT payload FROM monitoring_snapshot WHERE id = 1 FOR UPDATE"
            ).fetchone()
            if row is None:
                raise RuntimeError("monitoring snapshot is not initialized")
            snapshot, result = mutator(row[0])
            connection.execute(
                "UPDATE monitoring_snapshot SET payload = %s::jsonb, updated_at = NOW() WHERE id = 1",
                (json.dumps(snapshot, ensure_ascii=False),),
            )
            return (snapshot, result), False

    def observations(self, room_id=None, device_id=None, limit=100) -> list[dict]:
        clauses, params = [], []
        if room_id is not None:
            clauses.append("room_id = %s"); params.append(room_id)
        if device_id is not None:
            clauses.append("device_id = %s"); params.append(device_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT observation_id, room_id, device_id, state, confidence, "
                "captured_at, model_version, sequence_no, received_at FROM sensor_observations" + where +
                " ORDER BY captured_at DESC, received_at DESC LIMIT %s", params
            ).fetchall()
        return [{"observation_id": row[0], "room_id": row[1], "device_id": row[2],
                 "state": row[3], "confidence": row[4], "captured_at": row[5].isoformat(),
                 "model_version": row[6], "sequence_no": row[7],
                 "received_at": row[8].isoformat()} for row in rows]

    def device_statuses(self) -> list[dict]:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT DISTINCT ON (device_id) device_id, room_id, captured_at,
                          state, model_version, sequence_no, received_at
                   FROM sensor_observations
                   ORDER BY device_id, received_at DESC, captured_at DESC"""
            ).fetchall()
        now = datetime.now(timezone.utc)
        return [{"device_id": row[0], "room_id": row[1], "last_seen_at": row[2].isoformat(),
                 "state": row[3], "model_version": row[4], "sequence_no": row[5],
                 "last_received_at": row[6].isoformat(),
                 "online": (now - row[6]).total_seconds() <= DEVICE_ONLINE_SECONDS}
                for row in rows]

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

    def register_guardian_session(
        self, token_hash: str, patient_id: str, expires_at: datetime
    ) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "DELETE FROM guardian_sessions WHERE expires_at <= NOW()"
            )
            connection.execute(
                """
                INSERT INTO guardian_sessions
                    (token_hash, patient_id, created_at, expires_at)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (token_hash) DO UPDATE
                SET patient_id = EXCLUDED.patient_id,
                    created_at = NOW(),
                    expires_at = EXCLUDED.expires_at
                """,
                (token_hash, patient_id, expires_at),
            )

    def guardian_session_exists(self, token_hash: str) -> bool:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM guardian_sessions
                WHERE token_hash = %s AND expires_at > NOW()
                """,
                (token_hash,),
            ).fetchone()
            return row is not None

    def revoke_guardian_session(self, token_hash: str) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "DELETE FROM guardian_sessions WHERE token_hash = %s",
                (token_hash,),
            )

    def register_staff_session(
        self, token_hash: str, role: str, expires_at: datetime
    ) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute("DELETE FROM staff_sessions WHERE expires_at <= NOW()")
            connection.execute(
                """
                INSERT INTO staff_sessions (token_hash, role, created_at, expires_at)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (token_hash) DO UPDATE
                SET role = EXCLUDED.role,
                    created_at = NOW(),
                    expires_at = EXCLUDED.expires_at
                """,
                (token_hash, role, expires_at),
            )

    def staff_session_exists(self, token_hash: str) -> bool:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM staff_sessions
                WHERE token_hash = %s AND expires_at > NOW()
                """,
                (token_hash,),
            ).fetchone()
            return row is not None

    def revoke_staff_session(self, token_hash: str) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "DELETE FROM staff_sessions WHERE token_hash = %s",
                (token_hash,),
            )

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

    def record_observation(self, observation: SensorObservation) -> dict:
        with self._lock:
            def mutate(snapshot):
                latest = MonitoringStore()
                latest.import_snapshot(snapshot)
                status = latest.update_room(
                    observation.room_id, observation.state, observation.confidence
                )
                return latest.export_snapshot(), status

            value, duplicate = self.repository.record_observation(observation, mutate)
            if duplicate:
                snapshot = value
                self.memory.import_snapshot(snapshot)
                status = self.memory.room(observation.room_id)
            else:
                snapshot, status = value
                self.memory.import_snapshot(snapshot)
            return {**status, "observation_id": observation.observation_id,
                    "duplicate": duplicate}

    def observations(self, room_id=None, device_id=None, limit=100) -> list[dict]:
        return self.repository.observations(room_id, device_id, limit)

    def device_statuses(self) -> list[dict]:
        return self.repository.device_statuses()

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
        view = self._read("guardian_view", room_id)
        view["sensor_online"] = any(
            device["room_id"] == room_id and device["online"]
            for device in self.repository.device_statuses()
        )
        return view

    def guardian_events(self, room_id: str) -> list[dict]:
        return self._read("guardian_events", room_id)

