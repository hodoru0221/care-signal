import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.domain import MonitoringStore


app = FastAPI(title="WiFi Sensing Ward Monitor", version="0.1.0")
store = MonitoringStore()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
GUARDIAN_CONNECTION_CODE = os.getenv("GUARDIAN_CONNECTION_CODE", "CARE-101")
STAFF_ACCESS_CODE = os.getenv("STAFF_ACCESS_CODE", "NURSE-101")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "dev-device-key")
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
    room_id: str = "room-01"
    state: str
    confidence: float = Field(ge=0, le=1)


class EventUpdate(BaseModel):
    status: str
    actor: str = "demo-nurse"


class GuardianLogin(BaseModel):
    connection_code: str


class StaffLogin(BaseModel):
    access_code: str


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
    return {"status": "ok", "service": "care-signal-api"}


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


@app.post("/api/v1/inference")
def receive_inference(payload: InferenceInput, x_device_key: str = Header(default="")):
    if not secrets.compare_digest(x_device_key, DEVICE_API_KEY):
        raise HTTPException(401, "장치 인증에 실패했습니다.")
    try:
        return store.update_room(payload.room_id, payload.state, payload.confidence)
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
        return store.update_event(event_id, payload.status, payload.actor)
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


@app.post("/api/v1/staff/login")
def staff_login(payload: StaffLogin):
    if not secrets.compare_digest(payload.access_code.strip().upper(), STAFF_ACCESS_CODE.upper()):
        raise HTTPException(401, "직원 인증 코드를 확인해 주세요.")
    token = secrets.token_urlsafe(32)
    STAFF_SESSIONS.add(token)
    return {"access_token": token, "role": "NURSE"}
