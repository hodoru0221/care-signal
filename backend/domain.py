from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional
from uuid import uuid4

from backend.ward import WARD_ROOM_IDS


VALID_STATES = {"EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"}
ACTIVE_EVENT_STATUSES = {"OPEN", "ACKNOWLEDGED", "RESPONDING"}
MAX_HISTORY_PER_ROOM = 500


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


class MonitoringStore:
    """Thread-safe in-memory store used by the prototype and unit tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._rooms: Dict[str, RoomStatus] = {
            room_id: RoomStatus(room_id=room_id) for room_id in WARD_ROOM_IDS
        }
        self._events: Dict[str, Event] = {}
        self._history: Dict[str, list[dict]] = {room_id: [] for room_id in WARD_ROOM_IDS}

    def export_snapshot(self) -> dict:
        with self._lock:
            return {
                "rooms": {key: asdict(value) for key, value in self._rooms.items()},
                "events": {key: asdict(value) for key, value in self._events.items()},
                "history": self._history,
            }

    def import_snapshot(self, snapshot: dict) -> None:
        with self._lock:
            rooms = snapshot.get("rooms", {})
            events = snapshot.get("events", {})
            history = snapshot.get("history", {})
            self._rooms = {key: RoomStatus(**value) for key, value in rooms.items()}
            self._events = {key: Event(**value) for key, value in events.items()}
            self._history = {
                key: list(values)[-MAX_HISTORY_PER_ROOM:]
                for key, values in history.items()
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
        if state not in VALID_STATES:
            raise ValueError(f"Unknown state: {state}")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        with self._lock:
            previous = self._rooms.get(room_id)
            risk = self._risk_for(state)
            current = RoomStatus(room_id, state, confidence, risk, now_iso())
            self._rooms[room_id] = current
            room_history = self._history.setdefault(room_id, [])
            room_history.append(asdict(current))
            if len(room_history) > MAX_HISTORY_PER_ROOM:
                del room_history[:-MAX_HISTORY_PER_ROOM]

            changed_to_risk = risk != "NORMAL" and (
                previous is None or previous.state != state
            )
            if changed_to_risk and not self._has_active_event(room_id, state):
                event = Event(
                    id=f"evt-{uuid4().hex[:8]}",
                    room_id=room_id,
                    event_type=state,
                    risk_level=risk,
                    confidence=confidence,
                    status="OPEN",
                    created_at=now_iso(),
                )
                self._events[event.id] = event
            return asdict(current)

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
            return list(reversed(self._history.get(room_id, [])[-safe_limit:]))

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
                "sensor_online": True,
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
