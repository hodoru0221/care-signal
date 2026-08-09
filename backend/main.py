import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.domain import MonitoringStore
from backend.persistence import (
    PersistentMonitoringStore,
    PostgresSnapshotRepository,
    migrate_repository_if_empty,
)
from backend.notifications import send_push_notifications
from backend.ward import WARD_ROOM_IDS, ward_map_payload


app = FastAPI(title="WiFi Sensing Ward Monitor", version="0.2.0")
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "")
LEGACY_DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_URL = NEON_DATABASE_URL or LEGACY_DATABASE_URL
if DATABASE_URL:
    postgres_repository = PostgresSnapshotRepository(DATABASE_URL)
    if NEON_DATABASE_URL and LEGACY_DATABASE_URL and NEON_DATABASE_URL != LEGACY_DATABASE_URL:
        legacy_repository = PostgresSnapshotRepository(LEGACY_DATABASE_URL)
        migrate_repository_if_empty(postgres_repository, legacy_repository)
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
GUARDIAN_SESSIONS: set[str] = set()
STAFF_SESSIONS: set[str] = set()

allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allowed_origins != ["*"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Key"],
)


class InferenceInput(BaseModel):
    room_id: str = Field(default="room-01", min_length=1, max_length=32)
    state: Literal["EMPTY", "IN_BED", "OUT_OF_BED", "MOVEMENT_ANOMALY"]
    confidence: float = Field(ge=0, le=1)


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


def apply_state(room_id: str, state: str, confidence: float) -> dict:
    before = {event["id"] for event in store.events()}
    result = store.update_room(room_id, state, confidence)
    created = next((event for event in store.events() if event["id"] not in before), None)
    if created:
        notify_guardians(created["event_type"], created["id"])
    return result


def bearer_token(authorization: str) -> str:
    prefix = "Bearer "
    return authorization[len(prefix) :] if authorization.startswith(prefix) else ""


def require_guardian(authorization: str) -> None:
    if bearer_token(authorization) not in GUARDIAN_SESSIONS:
        raise HTTPException(401, "로그인이 필요합니다.")


def require_staff(authorization: str) -> None:
    if bearer_token(authorization) not in STAFF_SESSIONS:
        raise HTTPException(401, "직원 로그인이 필요합니다.")


@app.get("/")
def dashboard():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": "care-signal-api", "storage": STORAGE_MODE}


@app.get("/guardian")
def guardian_app():
    return FileResponse(WEB_DIR / "guardian.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(WEB_DIR / "service-worker.js", media_type="application/javascript")


@app.get("/api/v1/rooms/{room_id}/status")
def room_status(room_id: str):
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
        return apply_state(payload.room_id, payload.state, payload.confidence)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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
def device_alert(device_id: str, room_id: str = "room-01", location: str = "room"):
    return {"device_id": device_id, **store.device_alert(room_id, location)}


@app.post("/api/v1/guardian/login")
def guardian_login(payload: GuardianLogin):
    if not secrets.compare_digest(payload.connection_code.strip().upper(), GUARDIAN_CONNECTION_CODE.upper()):
        raise HTTPException(401, "연결 코드를 확인해 주세요.")
    token = secrets.token_urlsafe(32)
    GUARDIAN_SESSIONS.add(token)
    return {"access_token": token, "patient_id": "patient-01"}


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
    STAFF_SESSIONS.add(token)
    return {"access_token": token, "role": "NURSE", "demo_mode": DEMO_MODE}


@app.post("/api/v1/demo/state")
def demo_state(payload: DemoStateInput, authorization: str = Header(default="")):
    require_staff(authorization)
    if not DEMO_MODE:
        raise HTTPException(404, "시연 모드가 비활성화되어 있습니다.")
    if payload.room_id not in WARD_ROOM_IDS:
        raise HTTPException(400, "병동 맵에 등록되지 않은 병실입니다.")
    try:
        return apply_state(payload.room_id, payload.state, payload.confidence)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

