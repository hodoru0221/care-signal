# WiFi Sensing 병실 안전 모니터

하드웨어 없이도 서버, 웹 대시보드와 경보 흐름을 개발할 수 있는 1차 프로토타입이다.

## 현재 구현 문서

- `SYSTEM_DESIGN.md`: 실제 모델 연결을 가정한 전체 시스템·데이터·API·보안 설계
- `MODEL_INTEGRATION.md`: 모델 JSONL 표준화와 장애 복구 게이트웨이 사용법
- `DEMO_RUNBOOK.md`: 서버부터 모델·보호자 앱·UNO R4까지 통합 시연 순서
- `hardware/uno_r4_alert/README.md`: UNO R4 WiFi 현장 알림기 연결과 검증 한계

## 최종 접속 구조

공개 HTTPS 서버 하나를 중심으로 다음 클라이언트가 연결된다.

- 병원 직원 웹: `/`
- 보호자 모바일 웹: `/guardian`
- 보호자 Android/iOS 앱: `EXPO_PUBLIC_API_URL`에 설정한 API
- 병원 수집 PC: 장치 키를 사용해 `/api/v1/inference`로 결과 업로드

같은 WiFi 조건은 로컬 개발에서만 필요하다. 공개 배포 후에는 인터넷이 되는 곳에서 접속할 수 있다.

## 실행

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 연다. 다른 터미널에서 가상 분류기를 실행한다.

- 간호사용 대시보드: `http://127.0.0.1:8000/`
- 보호자 모바일 화면: `http://127.0.0.1:8000/guardian`
- 보호자 시연 연결 코드: `CARE-101`

## 병동 배치도 대시보드

직원 로그인 후 101~106호와 간호 스테이션이 표시된 병동 배치도를 볼 수 있다. 병실별 색상은 현재 위험도를 나타내며, 병실을 선택하면 최근 감지 기록과 해당 병실의 사건 대응 상태가 함께 표시된다.

- 초록: 정상 또는 환자 부재
- 주황: 침대 이탈 주의
- 빨강: 이상 움직임 위험
- `GET /api/v1/ward/map`: 배치 정보와 전체 병실의 현재 상태
- `GET /api/v1/rooms/{room_id}/history`: 병실별 최근 감지 기록

병동 배치는 `backend/ward.py`에서 관리하고 감지 기록은 모니터링 스냅샷에 저장한다. 기존 스냅샷을 불러올 때 새 병실과 이력 구조가 자동으로 보완되므로 이전 데이터와 호환된다. 병실별 최근 기록은 스냅샷 크기가 계속 커지지 않도록 최대 500건을 유지한다.

모델이 보낸 판정 원본 메타데이터는 별도 `sensor_observations` 테이블에 append-only로 저장한다. `observation_id`가 같으면 재전송으로 판단해 병실 이력과 사건을 다시 만들지 않는다. 직원 화면에서는 장치 온라인 여부, 마지막 수신, 모델 버전과 판정 지연을 확인할 수 있다.

### 모델 성능 평가

정답 라벨이 포함된 JSONL에서 혼동행렬, 정확도, macro F1, 상태별 precision/recall/F1과 수신 지연을 계산한다.

```powershell
python -m tools.evaluate_model evaluation.jsonl --output evaluation-report.json
```

병동 맵 응답은 정적 배치와 실시간 상태를 분리해 결합한다. 최상위에는 `id`, `name`, `rooms`, `stations`가 있고, 각 `rooms[]` 항목에는 `room_id`, 표시용 `label`·`bed_label`, 그리드 좌표 `x`·`y`·`width`·`height`, 그리고 `status`가 있다. `status`는 `room_id`, `state`, `confidence`, `risk_level`, `updated_at`을 담는다. 따라서 배치 변경은 과거 감지·사건 데이터 형식을 바꾸지 않는다.

## 최종 시연 흐름

1. 서버를 `DEMO_MODE=true`로 실행하고 직원 화면 `/`에서 `NURSE-101`로 로그인한다.
2. 병동 맵에서 병실을 선택한 뒤 `침대 위`를 눌러 정상 상태와 해당 병실 이력을 확인한다.
3. `침대 이탈` 또는 `이상 움직임`을 눌러 주황·빨강 상태, 사건 생성, UNO 경보 조회 결과를 확인한다.
4. 사건 카드에서 `확인` 또는 `이동 중`을 누르면 병실 경보음이 중지되고, 보호자 화면 `/guardian`에서 대응 진행 상태가 표시되는지 확인한다.
5. `처리 완료` 또는 `오탐`으로 사건을 종료한 뒤, 다른 병실을 선택해 병실별 상태와 이력이 서로 섞이지 않는지 확인한다.

시연 버튼은 `DEMO_MODE=true`에서만 보인다. 로그인 세션은 서버 메모리에만 있고, 상태·사건·병실 이력은 데이터베이스가 설정된 배포에서는 하나의 JSONB 스냅샷으로 저장된다.

## 자동화 테스트

테스트는 표준 `unittest`와 FastAPI HTTP 테스트 클라이언트를 사용한다. 운영 의존성과 분리된 테스트 의존성은 `requirements-test.txt`에만 추가되어 있다.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

인증 경계, 병동 맵 응답, 다중 병실 상태·사건 분리, 병실 이력 순서와 제한, 구형 스냅샷 보완, 잘못된 상태·신뢰도·병실·인증 입력을 검증한다. 알려진 결함을 재현하는 테스트는 `expected failure`로 별도 표시된다.

GitHub PR과 `main` 푸시에서는 `.github/workflows/ci.yml`이 Python 테스트·컴파일, 직원/보호자 웹 스크립트 문법, 모바일 TypeScript를 자동 검사한다. 실제 장비 테스트 전후의 전체 확인 항목은 `SOFTWARE_RELEASE_CHECKLIST.md`를 따른다.

## 보호자 네이티브 앱

`mobile` 폴더에는 Android와 iOS 공용 Expo 앱이 있다. Node.js 설치 후 다음과 같이 실행한다.

```powershell
cd mobile
npm install
npx expo start
```

휴대폰에 Expo Go를 설치하고 표시된 QR 코드를 스캔한다. 앱의 서버 주소에는 `127.0.0.1`이 아니라 서버 PC의 같은 WiFi 내부 IP를 입력한다.

배포 앱은 `mobile/.env`에 공개 API를 설정한다. 이 값이 있으면 사용자가 서버 주소를 입력하지 않는다.

```text
EXPO_PUBLIC_API_URL=https://api.example.com
```

현재 배포 앱의 공개 API는 `https://care-signal.onrender.com`으로 설정되어 있다. 이 파일에는 공개 주소만 저장하며 장치 키나 로그인 비밀값을 넣지 않는다.

설치형 Android 앱은 `mobile/eas.json`의 `preview` 프로필로 APK를 만든다. 이 APK는 Expo Go나 개발 PC 없이 실행되며 WiFi 또는 모바일 데이터로 공개 API에 연결한다.

```powershell
cd mobile
eas build --platform android --profile preview
```

앱이 화면에 열려 있을 때는 15초마다 상태와 이력을 갱신한다. 연결 실패 시 최대 60초 간격의 지수 백오프로 자동 재시도하고, 앱이 다시 활성화되거나 푸시 알림을 열면 즉시 최신 상태를 조회한다. 마지막 상태와 최근 이력 5건은 암호화된 로컬 저장소에 캐시하므로 앱을 완전히 종료한 뒤 오프라인으로 열어도 마지막 성공 동기화 화면을 확인할 수 있다. 배포 DB에서는 보호자 세션을 30일, 직원 세션을 12시간 보존하므로 Render 재시작만으로 로그아웃되지 않는다.

### 팀 시연 제어판

Render의 `DEMO_MODE=true`일 때 병원 직원 웹에 환자 부재, 침대 위, 침대 이탈, 이상 움직임 버튼이 표시된다. 직원 인증을 통과해야 사용할 수 있으며 장치 인증키는 브라우저에 노출되지 않는다. 실제 운영 전에는 `DEMO_MODE=false`로 변경한다.

### 이 PC에서 한 번에 테스트

프로젝트 루트의 `START_MOBILE_DEMO.ps1`을 PowerShell로 실행하면 서버, 가상 센서와 Expo 앱이 각각 새 창에서 실행된다. 세 창을 모두 종료하면 테스트가 끝난다.

## 공개 서버 배포 설정

`.env.example`을 기준으로 배포 서비스의 환경변수를 설정한다. 실제 비밀값은 `.env`나 Git 저장소에 올리지 않는다.

```text
GUARDIAN_CONNECTION_CODE=<보호자 연결 코드>
STAFF_ACCESS_CODE=<병원 직원 코드>
DEVICE_API_KEY=<병원 게이트웨이 장치 키>
ALLOWED_ORIGINS=https://hospital.example.com,https://guardian.example.com
```

서버는 `Dockerfile`로 컨테이너 배포할 수 있으며 배포 후 `/health`가 `status: ok`를 반환해야 한다.
`/health`의 `storage`가 `neon-postgres`인지, `revision`이 배포하려는 Git 커밋과 일치하는지도 함께 확인한다.

### Render 배포

저장소 루트의 `render.yaml`을 Blueprint로 연결하면 Singapore 리전의 Docker 웹 서비스가 생성된다. 최초 생성 화면에서 `GUARDIAN_CONNECTION_CODE`, `STAFF_ACCESS_CODE`, `DEVICE_API_KEY` 세 값을 입력한다. Render가 제공하는 `onrender.com` 주소는 HTTPS가 적용된 공개 접속 주소다.

Render Blueprint는 `care-signal-db` PostgreSQL을 함께 만들고 `DATABASE_URL`을 서버에 자동 연결한다. 환자 상태, 사건 기록과 로그인 세션은 서버 재시작 후에도 복원된다. 세션 토큰 원문은 저장하지 않고 SHA-256 해시와 만료 시각만 저장한다.

## 병원 수집 PC 게이트웨이

모델은 `inference.jsonl`에 한 줄씩 결과를 추가하고 게이트웨이는 이를 공개 서버로 보낸다.

```json
{"room_id":"room-01","state":"IN_BED","confidence":0.91}
```

```powershell
python gateway/uploader.py --api-url https://api.example.com --device-key <장치키>
```

인터넷 전송에 실패한 결과는 `pending_uploads.jsonl`에 보관하고 연결이 회복되면 재전송한다.

```powershell
python -m backend.simulator
```

## 팀원 하드웨어 테스트

실제 분류기 또는 CSI 수집 프로그램은 다음 API로 결과를 보낸다.

```http
POST /api/v1/inference
Content-Type: application/json

{"room_id":"room-01","state":"IN_BED","confidence":0.91}
```

UNO R4 알림기는 다음 주소를 1초 간격으로 조회한다.

```text
GET /api/v1/devices/uno-room-01/alert?room_id=room-01&location=room
```

이 요청에는 서버의 `DEVICE_API_KEY`와 같은 값을 `X-Device-Key` 헤더로 보내야 한다.
