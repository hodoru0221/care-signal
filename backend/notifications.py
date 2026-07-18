import json
from urllib.request import Request, urlopen


EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def notification_message(kind: str) -> tuple[str, str]:
    messages = {
        "OUT_OF_BED": ("병동 확인 요청", "침대 이탈 징후가 감지되어 병동에 확인을 요청했습니다."),
        "MOVEMENT_ANOMALY": ("긴급 확인 요청", "이상 움직임이 감지되어 병동 담당자에게 전달했습니다."),
        "ACKNOWLEDGED": ("담당자 확인", "병동 담당자가 알림을 확인했습니다."),
        "RESPONDING": ("상태 확인 중", "담당자가 환자 상태를 확인하고 있습니다."),
        "COMPLETED": ("확인 완료", "병동의 환자 상태 확인이 완료되었습니다."),
        "FALSE_ALARM": ("확인 완료", "확인 결과 추가 이상 징후가 없습니다."),
    }
    return messages.get(kind, ("환자 상태 알림", "환자 상태 정보가 갱신되었습니다."))


def build_push_messages(tokens: list[str], kind: str, event_id: str | None) -> list[dict]:
    title, body = notification_message(kind)
    return [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": {"event_id": event_id, "screen": "status"},
            "priority": "high",
        }
        for token in tokens
        if token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")
    ]


def send_push_notifications(tokens: list[str], kind: str, event_id: str | None) -> None:
    messages = build_push_messages(tokens, kind, event_id)
    if not messages:
        return
    request = Request(
        EXPO_PUSH_URL,
        data=json.dumps(messages).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10):
        pass
