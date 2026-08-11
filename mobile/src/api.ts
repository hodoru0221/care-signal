export const DISPLAY_STATES = ["STABLE", "AWAY", "WARD_NOTIFIED", "STAFF_CHECKING", "CHECK_COMPLETED"] as const;
export const EVENT_PROGRESS = ["OPEN", "ACKNOWLEDGED", "RESPONDING", "COMPLETED", "FALSE_ALARM"] as const;

export type DisplayState = (typeof DISPLAY_STATES)[number];
export type EventProgress = (typeof EVENT_PROGRESS)[number];

export type GuardianStatus = {
  patient: { display_name: string; room_label: string };
  display_state: DisplayState;
  message: string;
  sensor_online: boolean;
  updated_at: string;
  event: null | { id: string; progress: EventProgress; created_at: string; completed_at: string | null };
};

export type GuardianEvent = {
  id: string;
  progress: EventProgress;
  created_at: string;
  completed_at: string | null;
  summary: string;
};

export type GuardianCache = {
  status: GuardianStatus;
  history: GuardianEvent[];
  cached_at: string;
};

export type ApiErrorKind = "CONFIG" | "OFFLINE" | "TIMEOUT" | "SESSION_EXPIRED" | "SERVER" | "INVALID_RESPONSE";

export class ApiError extends Error {
  constructor(public readonly kind: ApiErrorKind, message: string, public readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export function normalizeAndValidateBaseUrl(input: string): string {
  const value = input.trim().replace(/\/+$/, "");
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new ApiError("CONFIG", "서버 주소 형식을 확인해 주세요.");
  }
  if (!parsed.hostname || parsed.username || parsed.password || !["http:", "https:"].includes(parsed.protocol)) {
    throw new ApiError("CONFIG", "http 또는 https 서버 주소를 입력해 주세요.");
  }
  if (!__DEV__ && parsed.protocol !== "https:") {
    throw new ApiError("CONFIG", "배포 앱은 안전한 HTTPS 서버 주소만 사용할 수 있습니다.");
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isProgress(value: unknown): value is EventProgress {
  return isString(value) && (EVENT_PROGRESS as readonly string[]).includes(value);
}

function parseEvent(value: unknown): GuardianEvent {
  if (!isRecord(value) || !isString(value.id) || !isProgress(value.progress) || !isString(value.created_at) || !isNullableString(value.completed_at) || !isString(value.summary)) {
    throw new ApiError("INVALID_RESPONSE", "서버 응답을 확인할 수 없습니다.");
  }
  return { id: value.id, progress: value.progress, created_at: value.created_at, completed_at: value.completed_at, summary: value.summary };
}

function parseStatus(value: unknown): GuardianStatus {
  if (!isRecord(value) || !isRecord(value.patient) || !isString(value.patient.display_name) || !isString(value.patient.room_label) ||
      !isString(value.display_state) || !(DISPLAY_STATES as readonly string[]).includes(value.display_state) || !isString(value.message) ||
      typeof value.sensor_online !== "boolean" || !isString(value.updated_at)) {
    throw new ApiError("INVALID_RESPONSE", "서버 응답을 확인할 수 없습니다.");
  }
  let event: GuardianStatus["event"] = null;
  if (value.event !== null) {
    if (!isRecord(value.event) || !isString(value.event.id) || !isProgress(value.event.progress) || !isString(value.event.created_at) || !isNullableString(value.event.completed_at)) {
      throw new ApiError("INVALID_RESPONSE", "서버 응답을 확인할 수 없습니다.");
    }
    event = { id: value.event.id, progress: value.event.progress, created_at: value.event.created_at, completed_at: value.event.completed_at };
  }
  return {
    patient: { display_name: value.patient.display_name, room_label: value.patient.room_label },
    display_state: value.display_state as DisplayState,
    message: value.message,
    sensor_online: value.sensor_online,
    updated_at: value.updated_at,
    event,
  };
}

async function request(baseUrl: string, path: string, init: RequestInit = {}, timeoutMs = 10_000): Promise<unknown> {
  const url = `${normalizeAndValidateBaseUrl(baseUrl)}${path}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    if (response.status === 401 && path !== "/api/v1/guardian/login") {
      throw new ApiError("SESSION_EXPIRED", "보호자 연결이 만료되었습니다.", 401);
    }
    if (!response.ok) {
      const loginMessage = path === "/api/v1/guardian/login" && response.status === 401
        ? "연결 코드를 확인해 주세요."
        : "서버에서 정보를 불러오지 못했습니다.";
      throw new ApiError("SERVER", loginMessage, response.status);
    }
    try {
      return await response.json();
    } catch {
      throw new ApiError("INVALID_RESPONSE", "서버 응답을 확인할 수 없습니다.");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError") throw new ApiError("TIMEOUT", "서버 응답이 지연되고 있습니다.");
    throw new ApiError("OFFLINE", "서버에 연결할 수 없습니다. 네트워크를 확인해 주세요.");
  } finally {
    clearTimeout(timeout);
  }
}

function authorization(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export async function login(baseUrl: string, connectionCode: string) {
  const payload = await request(baseUrl, "/api/v1/guardian/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_code: connectionCode.trim() }),
  });
  if (!isRecord(payload) || !isString(payload.access_token) || !isString(payload.patient_id)) {
    throw new ApiError("INVALID_RESPONSE", "로그인 응답을 확인할 수 없습니다.");
  }
  return { access_token: payload.access_token, patient_id: payload.patient_id };
}

export async function getPatient(baseUrl: string, token: string) {
  return parseStatus(await request(baseUrl, "/api/v1/guardian/patient", { headers: authorization(token) }));
}

export async function getHistory(baseUrl: string, token: string) {
  const payload = await request(baseUrl, "/api/v1/guardian/events", { headers: authorization(token) });
  if (!Array.isArray(payload)) throw new ApiError("INVALID_RESPONSE", "사건 이력 응답을 확인할 수 없습니다.");
  return payload.map(parseEvent);
}

export async function savePushToken(baseUrl: string, accessToken: string, pushToken: string) {
  await request(baseUrl, "/api/v1/guardian/push-token", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authorization(accessToken) },
    body: JSON.stringify({ token: pushToken }),
  });
}

export function encodeGuardianCache(
  status: GuardianStatus,
  history: GuardianEvent[],
  cachedAt = new Date(),
): string {
  return JSON.stringify({
    version: 1,
    status,
    history: history.slice(0, 5),
    cached_at: cachedAt.toISOString(),
  });
}

export function parseGuardianCache(value: string | null): GuardianCache | null {
  if (!value) return null;
  try {
    const payload: unknown = JSON.parse(value);
    if (!isRecord(payload) || payload.version !== 1 || !isString(payload.cached_at) || !Array.isArray(payload.history)) return null;
    const cachedAt = new Date(payload.cached_at);
    if (Number.isNaN(cachedAt.getTime())) return null;
    return {
      status: parseStatus(payload.status),
      history: payload.history.slice(0, 5).map(parseEvent),
      cached_at: cachedAt.toISOString(),
    };
  } catch {
    return null;
  }
}

export async function logout(baseUrl: string, accessToken: string) {
  await request(baseUrl, "/api/v1/guardian/logout", {
    method: "POST",
    headers: authorization(accessToken),
  });
}
