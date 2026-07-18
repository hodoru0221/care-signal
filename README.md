# WiFi Sensing 병실 안전 모니터

하드웨어 없이도 서버, 웹 대시보드와 경보 흐름을 개발할 수 있는 1차 프로토타입이다.

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

현재 로그인 세션과 사건 기록은 메모리에 저장되는 프로토타입이다. 서버가 재시작되어도 유지되는 운영 버전을 만들려면 다음 단계에서 PostgreSQL과 사용자 계정 테이블을 연결해야 한다.

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

