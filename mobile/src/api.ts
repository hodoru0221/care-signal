export type GuardianStatus = {
  patient: { display_name: string; room_label: string };
  display_state: "STABLE" | "AWAY" | "WARD_NOTIFIED" | "STAFF_CHECKING" | "CHECK_COMPLETED";
  message: string;
  sensor_online: boolean;
  updated_at: string;
  event: null | { id: string; progress: string; created_at: string; completed_at: string | null };
};

export type GuardianEvent = {
  id: string;
  progress: string;
  created_at: string;
  completed_at: string | null;
  summary: string;
};

function normalizeBaseUrl(url: string) {
  return url.trim().replace(/\/$/, "");
}

export async function login(baseUrl: string, connectionCode: string) {
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}/api/v1/guardian/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_code: connectionCode }),
  });
  if (!response.ok) throw new Error("연결 코드 또는 서버 주소를 확인해 주세요.");
  return response.json() as Promise<{ access_token: string; patient_id: string }>;
}

async function authorizedGet<T>(baseUrl: string, token: string, path: string): Promise<T> {
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) throw new Error("SESSION_EXPIRED");
  if (!response.ok) throw new Error("서버에서 정보를 불러오지 못했습니다.");
  return response.json() as Promise<T>;
}

export const getPatient = (baseUrl: string, token: string) =>
  authorizedGet<GuardianStatus>(baseUrl, token, "/api/v1/guardian/patient");

export const getHistory = (baseUrl: string, token: string) =>
  authorizedGet<GuardianEvent[]>(baseUrl, token, "/api/v1/guardian/events");

export async function savePushToken(baseUrl: string, accessToken: string, pushToken: string) {
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}/api/v1/guardian/push-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ token: pushToken }),
  });
  if (!response.ok) throw new Error("푸시 알림 등록에 실패했습니다.");
}
