from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from math import isfinite
from threading import Lock
from typing import Dict, Optional
from uuid import uuid4

from backend.ward import WARD_ROOM_IDS


VALID_STATES = {"EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"}
ACTIVE_EVENT_STATUSES = {"OPEN", "ACKNOWLEDGED", "RESPONDING"}
MAX_HISTORY_PER_ROOM = 500
MAX_EVENTS = 2000
MAX_OBSERVATIONS = 5000
DEVICE_ONLINE_SECONDS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RoomStatus:
    room_id: str = "room-01"
    state: str = "EMPTY"
    confidence: float = 1.0
    risk_level: str = "NORMAL"
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = now_iso()


@dataclass
class Event:
    id: str
    room_id: str
    event_type: str
    risk_level: str
    confidence: float
    status: str
    created_at: str
    acknowledged_at: Optional[str] = None
    completed_at: Optional[str] = None
    actor: Optional[str] = None


@dataclass(frozen=True)
class SensorObservation:
    observation_id: str
    room_id: str
    device_id: str
    state: str
    confidence: float
    captured_at: str
    model_version: str
    sequence_no: Optional[int] = None
    received_at: str = ""

    def __post_init__(self) -> None:
        if not self.received_at:
            object.__setattr__(self, "received_at", now_iso())


class MonitoringStore:
    """Thread-safe in-memory store used by the prototype and unit tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._rooms: Dict[str, RoomStatus] = {
            room_id: RoomStatus(room_id=room_id) for room_id in WARD_ROOM_IDS
        }
        self._events: Dict[str, Event] = {}
        self._history: Dict[str, list[dict]] = {room_id: [] for room_id in WARD_ROOM_IDS}
        self._observations: Dict[str, SensorObservation] = {}

    def export_snapshot(self) -> dict:
        with self._lock:
            return {
                "rooms": {key: asdict(value) for key, value in self._rooms.items()},
                "events": {key: asdict(value) for key, value in self._events.items()},
                "history": deepcopy(self._history),
            }

    def import_snapshot(self, snapshot: dict) -> None:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot must be an object")
        with self._lock:
            rooms = snapshot.get("rooms", {})
            events = snapshot.get("events", {})
            history = snapshot.get("history", {})
            if not isinstance(rooms, dict) or not isinstance(events, dict):
                raise ValueError("snapshot rooms and events must be objects")
            if not isinstance(history, dict):
                history = {}

            room_fields = {field.name for field in fields(RoomStatus)}
            event_fields = {field.name for field in fields(Event)}
            self._rooms = {}
            for room_id in WARD_ROOM_IDS:
                value = rooms.get(room_id)
                if not isinstance(value, dict):
                    continue
                room_values = {key: item for key, item in value.items() if key in room_fields}
                room_values["room_id"] = room_id
                self._rooms[room_id] = RoomStatus(**room_values)
            imported_events = []
            for event_id, value in events.items():
                if not isinstance(value, dict):
                    continue
                try:
                    event = Event(**{key: item for key, item in value.items() if key in event_fields})
                except (TypeError, ValueError):
                    continue
                if event.id != event_id or event.room_id not in WARD_ROOM_IDS:
                    continue
                imported_events.append(event)
            imported_events.sort(key=lambda event: event.created_at, reverse=True)
            self._events = {event.id: event for event in imported_events[:MAX_EVENTS]}
            self._history = {
                room_id: [item for item in values if isinstance(item, dict)][-MAX_HISTORY_PER_ROOM:]
                for room_id in WARD_ROOM_IDS
                if isinstance((values := history.get(room_id)), list)
            }
            for room_id in WARD_ROOM_IDS:
                if room_id not in self._rooms:
                    self._rooms[room_id] = RoomStatus(room_id=room_id)
                self._history.setdefault(room_id, [])

    @staticmethod
    def _risk_for(state: str) -> str:
        return {
            "EMPTY": "NORMAL",
            "IN_BED": "NORMAL",
            "OUT_OF_BED": "WARNING",
            "MOVEMENT_ANOMALY": "CRITICAL",
        }[state]

    def update_room(self, room_id: str, state: str, confidence: float) -> dict:
        self._validate_room_update(room_id, state, confidence)
        with self._lock:
            return self._update_room_locked(room_id, state, confidence)

    @staticmethod
    def _validate_room_update(room_id: str, state: str, confidence: float) -> None:
        if room_id not in WARD_ROOM_IDS:
            raise ValueError(f"Unknown room: {room_id}")
        if state not in VALID_STATES:
            raise ValueError(f"Unknown state: {state}")
        if isinstance(confidence, bool) or not isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def _update_room_locked(self, room_id: str, state: str, confidence: float) -> dict:
        previous = self._rooms.get(room_id)
        risk = self._risk_for(state)
        current = RoomStatus(room_id, state, confidence, risk, now_iso())
        self._rooms[room_id] = current
        room_history = self._history.setdefault(room_id, [])
        room_history.append(asdict(current))
        if len(room_history) > MAX_HISTORY_PER_ROOM:
            del room_history[:-MAX_HISTORY_PER_ROOM]

        changed_to_risk = risk != "NORMAL" and (previous is None or previous.state != state)
        if changed_to_risk and not self._has_active_event(room_id, state):
            event = Event(
                id=f"evt-{uuid4().hex[:8]}", room_id=room_id, event_type=state,
                risk_level=risk, confidence=confidence, status="OPEN", created_at=now_iso(),
            )
            self._events[event.id] = event
            self._trim_events()
        return asdict(current)

    def record_observation(self, observation: SensorObservation) -> dict:
        self._validate_room_update(observation.room_id, observation.state, observation.confidence)
        with self._lock:
            if observation.observation_id in self._observations:
                return {**asdict(self._rooms[observation.room_id]),
                        "observation_id": observation.observation_id, "duplicate": True}
            self._observations[observation.observation_id] = observation
            while len(self._observations) > MAX_OBSERVATIONS:
                del self._observations[next(iter(self._observations))]
            status = self._update_room_locked(
                observation.room_id, observation.state, observation.confidence
            )
            return {**status, "observation_id": observation.observation_id, "duplicate": False}

    def observations(
        self, room_id: Optional[str] = None, device_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        with self._lock:
            values = reversed(list(self._observations.values()))
            return [asdict(item) for item in values
                    if (room_id is None or item.room_id == room_id)
                    and (device_id is None or item.device_id == device_id)][:limit]

    def device_statuses(self) -> list[dict]:
        with self._lock:
            latest: dict[str, SensorObservation] = {}
            for item in self._observations.values():
                if item.device_id not in latest or item.received_at > latest[item.device_id].received_at:
                    latest[item.device_id] = item
            now = datetime.now(timezone.utc)
            return [{"device_id": item.device_id, "room_id": item.room_id,
                     "last_seen_at": item.captured_at, "last_received_at": item.received_at,
                     "online": (now - datetime.fromisoformat(item.received_at.replace("Z", "+00:00"))).total_seconds()
                     <= DEVICE_ONLINE_SECONDS,
                     "state": item.state, "model_version": item.model_version,
                     "sequence_no": item.sequence_no}
                    for item in sorted(latest.values(), key=lambda value: value.device_id)]

    def _trim_events(self) -> None:
        if len(self._events) <= MAX_EVENTS:
            return
        removable = sorted(
            (event for event in self._events.values() if event.status not in ACTIVE_EVENT_STATUSES),
            key=lambda event: event.created_at,
        )
        for event in removable[: len(self._events) - MAX_EVENTS]:
            del self._events[event.id]

    def _has_active_event(self, room_id: str, event_type: str) -> bool:
        return any(
            event.room_id == room_id
            and event.event_type == event_type
            and event.status in ACTIVE_EVENT_STATUSES
            for event in self._events.values()
        )

    def room(self, room_id: str) -> dict:
        with self._lock:
            if room_id not in self._rooms:
                raise KeyError(room_id)
            return asdict(self._rooms[room_id])

    def rooms(self) -> list[dict]:
        with self._lock:
            return [asdict(self._rooms[key]) for key in sorted(self._rooms)]

    def room_history(self, room_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            if room_id not in self._rooms:
                raise KeyError(room_id)
            safe_limit = max(1, min(limit, 200))
            return deepcopy(list(reversed(self._history.get(room_id, [])[-safe_limit:])))

    def events(self) -> list[dict]:
        with self._lock:
            ordered = sorted(self._events.values(), key=lambda x: x.created_at, reverse=True)
            return [asdict(event) for event in ordered]

    def guardian_view(self, room_id: str) -> dict:
        """Return a non-clinical, privacy-minimized view for a linked guardian."""
        with self._lock:
            if room_id not in self._rooms:
                raise KeyError(room_id)
            room = self._rooms[room_id]
            recent = sorted(
                (event for event in self._events.values() if event.room_id == room_id),
                key=lambda event: event.created_at,
                reverse=True,
            )
            event = recent[0] if recent else None
            now = datetime.now(timezone.utc)
            sensor_online = any(
                observation.room_id == room_id
                and (
                    now
                    - datetime.fromisoformat(
                        observation.received_at.replace("Z", "+00:00")
                    )
                ).total_seconds()
                <= DEVICE_ONLINE_SECONDS
                for observation in self._observations.values()
            )

            if event and event.status in {"ACKNOWLEDGED", "RESPONDING"}:
                display_state = "STAFF_CHECKING"
                message = "병동 담당자가 환자 상태를 확인하고 있습니다."
            elif event and event.status == "OPEN":
                display_state = "WARD_NOTIFIED"
                message = "이상 징후가 감지되어 병동에 확인을 요청했습니다."
            elif event and event.status in {"COMPLETED", "FALSE_ALARM"}:
                display_state = "CHECK_COMPLETED"
                message = "병동의 상태 확인이 완료되었습니다."
            elif room.state == "EMPTY":
                display_state = "AWAY"
                message = "현재 침대 영역에서 환자가 감지되지 않습니다."
            else:
                display_state = "STABLE"
                message = "현재 안정적으로 모니터링되고 있습니다."

            return {
                "patient": {"display_name": "김○○", "room_label": "101호"},
                "display_state": display_state,
                "message": message,
                "sensor_online": sensor_online,
                "updated_at": room.updated_at,
                "event": None
                if event is None
                else {
                    "id": event.id,
                    "progress": event.status,
                    "created_at": event.created_at,
                    "completed_at": event.completed_at,
                },
            }

    def guardian_events(self, room_id: str) -> list[dict]:
        with self._lock:
            ordered = sorted(
                (event for event in self._events.values() if event.room_id == room_id),
                key=lambda event: event.created_at,
                reverse=True,
            )
            return [
                {
                    "id": event.id,
                    "progress": event.status,
                    "created_at": event.created_at,
                    "completed_at": event.completed_at,
                    "summary": "병동 상태 확인",
                }
                for event in ordered[:20]
            ]

    def update_event(self, event_id: str, status: str, actor: str) -> dict:
        allowed = {"ACKNOWLEDGED", "RESPONDING", "COMPLETED", "FALSE_ALARM"}
        if status not in allowed:
            raise ValueError(f"Invalid event status: {status}")
        with self._lock:
            if event_id not in self._events:
                raise KeyError(event_id)
            event = self._events[event_id]
            event.status = status
            event.actor = actor
            if status == "ACKNOWLEDGED" and event.acknowledged_at is None:
                event.acknowledged_at = now_iso()
            if status in {"COMPLETED", "FALSE_ALARM"}:
                event.completed_at = now_iso()
            return asdict(event)

    def device_alert(self, room_id: str, location: str) -> dict:
        with self._lock:
            active = [
                event
                for event in self._events.values()
                if event.room_id == room_id and event.status == "OPEN"
            ]
            if not active:
                return {"level": "NORMAL", "sound": False, "event_id": None}
            event = max(active, key=lambda x: x.created_at)
            if location == "room" and event.risk_level == "WARNING":
                sound_pattern = "GENTLE_ONCE"
            else:
                sound_pattern = "URGENT_REPEAT"
            return {
                "level": event.risk_level,
                "sound": True,
                "sound_pattern": sound_pattern,
                "event_id": event.id,
            }
