import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from psycopg import OperationalError

from backend.domain import MonitoringStore, SensorObservation, now_iso
from backend.persistence import (
    PersistentMonitoringStore,
    PostgresSnapshotRepository,
    migrate_repository_if_empty,
)
from backend.notifications import send_push_notifications
from backend.ward import WARD_ROOM_IDS, ward_map_payload


app = FastAPI(title="WiFi Sensing Ward Monitor", version="0.2.0")
logger = logging.getLogger(__name__)
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "")
LEGACY_DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_URL = NEON_DATABASE_URL or LEGACY_DATABASE_URL
if DATABASE_URL:
    postgres_repository = PostgresSnapshotRepository(DATABASE_URL)
    if NEON_DATABASE_URL and LEGACY_DATABASE_URL and NEON_DATABASE_URL != LEGACY_DATABASE_URL:
        legacy_repository = PostgresSnapshotRepository(LEGACY_DATABASE_URL)
        try:
            migrate_repository_if_empty(postgres_repository, legacy_repository)
        except OperationalError as error:
            logger.warning("Legacy database migration skipped: %s", error)
    store = PersistentMonitoringStore(postgres_repository)
    push_tokens_memory = None
    STORAGE_MODE = "neon-postgres" if NEON_DATABASE_URL else "render-postgres"
else:
    store = MonitoringStore()
    postgres_repository = None
    push_tokens_memory: set[str] | None = set()
    STORAGE_MODE = "memory"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
GUARDIAN_CONNECTION_CODE = os.getenv("GUARDIAN_CONNECTION_CODE", "CARE-101")
STAFF_ACCESS_CODE = os.getenv("STAFF_ACCESS_CODE", "NURSE-101")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "dev-device-key")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
DEPLOY_REVISION = os.getenv("RENDER_GIT_COMMIT", os.getenv("GIT_COMMIT", "local"))[:12]
GUARDIAN_SESSIONS: set[str] = set()
STAFF_SESSIONS: set[str] = set()
try:
    GUARDIAN_SESSION_DAYS = max(1, min(int(os.getenv("GUARDIAN_SESSION_DAYS", "30")), 365))
except ValueError:
    GUARDIAN_SESSION_DAYS = 30
try:
    STAFF_SESSION_HOURS = max(1, min(int(os.getenv("STAFF_SESSION_HOURS", "12")), 72))
except ValueError:
    STAFF_SESSION_HOURS = 12

allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allowed_origins != ["*"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Key"],
)


class InferenceInput(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    room_id: str = Field(default="room-01", min_length=1, max_length=32)
    device_id: str = Field(default="legacy-device", min_length=1, max_length=128)
    state: Literal["EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"]
    confidence: float = Field(ge=0, le=1)
    captured_at: str = Field(default_factory=now_iso, min_length=1, max_length=64)
    model_version: str = Field(default="legacy", min_length=1, max_length=128)
    sequence_no: int | None = Field(default=None, ge=0)

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_must_be_finite(cls, value):
        if isinstance(value, float) and not isfinite(value):
            # Replace non-JSON-safe input before FastAPI renders a validation error.
            return 2.0
        return value

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_utc_iso8601(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("captured_at must be a valid UTC ISO8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("captured_at must include the UTC timezone")
        return parsed.astimezone(timezone.utc).isoformat()


class EventUpdate(BaseModel):
    status: Literal["ACKNOWLEDGED", "RESPONDING", "COMPLETED", "FALSE_ALARM"]
    actor: str = Field(default="demo-nurse", min_length=1, max_length=100)


class GuardianLogin(BaseModel):
    connection_code: str = Field(min_length=1, max_length=100)


class StaffLogin(BaseModel):
    access_code: str = Field(min_length=1, max_length=100)


class PushTokenInput(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class DemoStateInput(BaseModel):
    room_id: str = Field(default="room-01", min_length=1, max_length=32)
    state: Literal["EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"]
    confidence: float = Field(default=0.95, ge=0, le=1)


class DemoEventCleanupInput(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=100)
    confirmation: Literal["DELETE"]

    @field_validator("event_ids")
    @classmethod
    def event_ids_must_be_unique_and_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 128 for item in cleaned):
            raise ValueError("event_ids must contain valid identifiers")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("event_ids must be unique")
        return cleaned


def register_push_token(token: str) -> None:
    if not (token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")):
        raise HTTPException(400, "올바른 Expo Push Token이 아닙니다.")
    if postgres_repository:
        postgres_repository.register_push_token("patient-01", token)
    else:
        push_tokens_memory.add(token)


def patient_push_tokens() -> list[str]:
    if postgres_repository:
        return postgres_repository.push_tokens("patient-01")
    return list(push_tokens_memory)


def notify_guardians(kind: str, event_id: str | None) -> None:
    try:
        send_push_notifications(patient_push_tokens(), kind, event_id)
    except Exception:
        # Push delivery failure must not prevent clinical event processing.
        pass


def apply_observation(observation: SensorObservation) -> dict:
    before = {event["id"] for event in store.events()}
    result = store.record_observation(observation)
    if not result["duplicate"]:
        created = next((event for event in store.events() if event["id"] not in before), None)
        if created:
            notify_guardians(created["event_type"], created["id"])
    return result


def bearer_token(authorization: str) -> str:
    prefix = "Bearer "
    return authorization[len(prefix) :] if authorization.startswith(prefix) else ""


def session_token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def register_guardian_session(token: str) -> None:
    if postgres_repository:
        postgres_repository.register_guardian_session(
            session_token_hash(token),
            "patient-01",
            datetime.now(timezone.utc) + timedelta(days=GUARDIAN_SESSION_DAYS),
        )
    else:
        GUARDIAN_SESSIONS.add(token)


def require_guardian(authorization: str) -> None:
    token = bearer_token(authorization)
    valid = (
        postgres_repository.guardian_session_exists(session_token_hash(token))
        if postgres_repository and token
        else token in GUARDIAN_SESSIONS
    )
    if not valid:
        raise HTTPException(401, "로그인이 필요합니다.")


def require_staff(authorization: str) -> None:
    token = bearer_token(authorization)
    valid = (
        postgres_repository.staff_session_exists(session_token_hash(token))
        if postgres_repository and token
        else token in STAFF_SESSIONS
    )
    if not valid:
        raise HTTPException(401, "직원 로그인이 필요합니다.")


def register_staff_session(token: str) -> None:
    if postgres_repository:
        postgres_repository.register_staff_session(
            session_token_hash(token),
            "NURSE",
            datetime.now(timezone.utc) + timedelta(hours=STAFF_SESSION_HOURS),
        )
    else:
        STAFF_SESSIONS.add(token)


@app.get("/")
def dashboard():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "care-signal-api",
        "storage": STORAGE_MODE,
        "revision": DEPLOY_REVISION,
    }


@app.get("/guardian")
def guardian_app():
    return FileResponse(WEB_DIR / "guardian.html")


@app.get("/simulator")
def simulator_app():
    return FileResponse(WEB_DIR / "simulator.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(WEB_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/api/v1/rooms/{room_id}/status")
def room_status(room_id: str, authorization: str = Header(default="")):
    require_staff(authorization)
    try:
        return store.room(room_id)
    except KeyError:
        raise HTTPException(404, "Room not found")


@app.get("/api/v1/ward/map")
def ward_map(authorization: str = Header(default="")):
    require_staff(authorization)
    return ward_map_payload(store)


@app.get("/api/v1/rooms/{room_id}/history")
def room_history(
    room_id: str,
    limit: int = Query(default=30, ge=1, le=200),
    authorization: str = Header(default=""),
):
    require_staff(authorization)
    try:
        return store.room_history(room_id, limit)
    except KeyError:
        raise HTTPException(404, "Room not found")


@app.post("/api/v1/inference")
def receive_inference(payload: InferenceInput, x_device_key: str = Header(default="")):
    if not secrets.compare_digest(x_device_key, DEVICE_API_KEY):
        raise HTTPException(401, "장치 인증에 실패했습니다.")
    try:
        return apply_observation(SensorObservation(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/observations")
def observations(
    room_id: str | None = Query(default=None, max_length=32),
    device_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str = Header(default=""),
):
    require_staff(authorization)
    if room_id is not None and room_id not in WARD_ROOM_IDS:
        raise HTTPException(400, f"Unknown room: {room_id}")
    return store.observations(room_id, device_id, limit)


@app.get("/api/v1/devices/status")
def devices_status(authorization: str = Header(default="")):
    require_staff(authorization)
    return store.device_statuses()


@app.get("/api/v1/events")
def events(authorization: str = Header(default="")):
    require_staff(authorization)
    return store.events()


@app.patch("/api/v1/events/{event_id}")
def update_event(event_id: str, payload: EventUpdate, authorization: str = Header(default="")):
    require_staff(authorization)
    try:
        result = store.update_event(event_id, payload.status, payload.actor)
        notify_guardians(payload.status, event_id)
        return result
    except KeyError:
        raise HTTPException(404, "Event not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/devices/{device_id}/alert")
def device_alert(
    device_id: str,
    room_id: str = "room-01",
    location: Literal["room", "nurse", "station"] = "room",
    x_device_key: str = Header(default=""),
):
    if not secrets.compare_digest(x_device_key, DEVICE_API_KEY):
        raise HTTPException(401, "장치 인증에 실패했습니다.")
    if room_id not in WARD_ROOM_IDS:
        raise HTTPException(400, "병동 맵에 등록되지 않은 병실입니다.")
    return {"device_id": device_id, **store.device_alert(room_id, location)}


@app.post("/api/v1/guardian/login")
def guardian_login(payload: GuardianLogin):
    if not secrets.compare_digest(payload.connection_code.strip().upper(), GUARDIAN_CONNECTION_CODE.upper()):
        raise HTTPException(401, "연결 코드를 확인해 주세요.")
    token = secrets.token_urlsafe(32)
    register_guardian_session(token)
    return {"access_token": token, "patient_id": "patient-01"}


@app.post("/api/v1/guardian/logout")
def guardian_logout(authorization: str = Header(default="")):
    require_guardian(authorization)
    token = bearer_token(authorization)
    if postgres_repository:
        postgres_repository.revoke_guardian_session(session_token_hash(token))
    else:
        GUARDIAN_SESSIONS.discard(token)
    return {"signed_out": True}


@app.get("/api/v1/guardian/patient")
def guardian_patient(authorization: str = Header(default="")):
    require_guardian(authorization)
    return store.guardian_view("room-01")


@app.get("/api/v1/guardian/events")
def guardian_events(authorization: str = Header(default="")):
    require_guardian(authorization)
    return store.guardian_events("room-01")


@app.post("/api/v1/guardian/push-token")
def guardian_push_token(payload: PushTokenInput, authorization: str = Header(default="")):
    require_guardian(authorization)
    register_push_token(payload.token)
    return {"registered": True}


@app.post("/api/v1/staff/login")
def staff_login(payload: StaffLogin):
    if not secrets.compare_digest(payload.access_code.strip().upper(), STAFF_ACCESS_CODE.upper()):
        raise HTTPException(401, "직원 인증 코드를 확인해 주세요.")
    token = secrets.token_urlsafe(32)
    register_staff_session(token)
    return {"access_token": token, "role": "NURSE", "demo_mode": DEMO_MODE}


@app.post("/api/v1/staff/logout")
def staff_logout(authorization: str = Header(default="")):
    require_staff(authorization)
    token = bearer_token(authorization)
    if postgres_repository:
        postgres_repository.revoke_staff_session(session_token_hash(token))
    else:
        STAFF_SESSIONS.discard(token)
    return {"signed_out": True}


@app.post("/api/v1/demo/state")
def demo_state(payload: DemoStateInput, authorization: str = Header(default="")):
    require_staff(authorization)
    if not DEMO_MODE:
        raise HTTPException(404, "시연 모드가 비활성화되어 있습니다.")
    if payload.room_id not in WARD_ROOM_IDS:
        raise HTTPException(400, "병동 맵에 등록되지 않은 병실입니다.")
    try:
        return apply_observation(SensorObservation(
            observation_id=f"demo-{uuid4()}",
            room_id=payload.room_id,
            device_id="demo-console",
            state=payload.state,
            confidence=payload.confidence,
            captured_at=now_iso(),
            model_version="demo-panel",
        ))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/demo/events/cleanup")
def demo_event_cleanup(
    payload: DemoEventCleanupInput,
    authorization: str = Header(default=""),
):
    require_staff(authorization)
    if not DEMO_MODE:
        raise HTTPException(404, "시연 모드가 비활성화되어 있습니다.")
    try:
        return store.delete_completed_events(payload.event_ids)
    except KeyError:
        raise HTTPException(404, "삭제할 사건을 찾을 수 없습니다.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

