# Care Signal 소프트웨어 릴리스 체크리스트

이 문서는 실제 CSI 장비 테스트 전후에 소프트웨어 기준 상태를 빠르게 확인하기 위한 체크리스트다.

## 현재 완료된 소프트웨어 범위

- 직원 인증, 보호자 연결과 로그아웃
- 병동 맵과 병실별 상태·감지 이력
- 사건 생성, 확인, 대응, 완료와 오탐 처리
- 완료 사건 선택 삭제와 2단계 삭제 확인
- 장치 인증 경계와 UNO R4 경보 조회 API
- 관측 중복 방지, PostgreSQL 영속화와 세션 해시 저장
- 보호자 앱 오프라인 캐시, 자동 재시도와 푸시 등록
- Render 자동 배포, Neon PostgreSQL, Expo/EAS 연결
- GitHub Actions 기반 Python·웹·모바일 자동 검사

## 코드 변경 전후 확인

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q backend gateway tools
cd mobile
pnpm install --frozen-lockfile
pnpm run typecheck
```

GitHub에서는 PR의 `Backend and gateway`, `Web and guardian mobile` 검사가 모두 통과한 뒤 `main`에 병합한다.

## 시연 직전

- Render `/health`가 `status: ok`, `storage: neon-postgres`를 반환하는지 확인한다.
- `/health`의 `revision`이 GitHub `main` 최신 커밋과 일치하는지 확인한다.
- 직원 화면에서 오래된 진행 중 사건을 처리 완료 또는 오탐으로 종료한다.
- 완료 이력에서 불필요한 시연 사건만 선택 삭제한다. 감지 원본은 삭제되지 않는다.
- 101호를 `IN_BED`, 나머지 병실을 팀 시연 시나리오에 맞는 초기 상태로 둔다.
- Android 앱이 `https://care-signal.onrender.com`에 연결되는지 확인한다.

## 실제 장비가 있어야 확인 가능한 항목

- CSI 모델 출력의 상태·confidence·시간 형식
- 모델의 연속 판정 기준과 confidence 임계값
- 센서 마지막 수신 후 30초 오프라인 표시
- UNO R4의 `GENTLE_ONCE`, `URGENT_REPEAT`와 직원 확인 후 정지
- 보호자 실기기의 푸시 알림 수신
- 병원 PC 인터넷 단절 후 게이트웨이 재전송과 중복 방지

## 시연 종료 후

- `DEMO_MODE=false`로 전환한다.
- 직원 코드, 보호자 연결 코드와 장치 API 키를 교체한다.
- 필요 없는 완료 시연 이력만 선택 삭제한다.
- Render와 Expo 빌드 로그에 비밀값이 출력되지 않았는지 확인한다.
