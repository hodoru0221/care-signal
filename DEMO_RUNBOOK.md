# Care Signal 통합 시연 실행서

이 문서는 실제 CSI 모델 또는 모델 출력 대체 파일에서 시작해 병동 맵, 사건 대응, 보호자 확인, UNO R4 알림까지 검증하는 순서다. 실제 비밀값을 문서나 명령 기록에 남기지 않는다.

## 1. 사전 확인

- Python 3.12, Node.js, Expo 환경
- 서버용 `.env` 또는 터미널 환경변수
- 모델 출력 JSONL
- 실제 장비 사용 시 CSI 수집 PC와 UNO R4 WiFi

필수 서버 환경변수 이름:

```text
GUARDIAN_CONNECTION_CODE
STAFF_ACCESS_CODE
DEVICE_API_KEY
ALLOWED_ORIGINS
DEMO_MODE
NEON_DATABASE_URL
```

로컬 메모리 모드에서는 DB URL을 설정하지 않아도 된다. 실제 연결 문자열과 키를 Git, 캡처 화면, 발표 자료에 넣지 않는다.

## 2. 설치와 전체 테스트

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

모든 테스트가 통과한 뒤 시연을 시작한다.

## 3. 서버 시작

```powershell
$env:DEMO_MODE='true'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 다음을 확인한다.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

DB 연결이 없으면 `memory`, Neon이 연결되면 `neon-postgres`가 표시된다.

## 4. 실제 모델 출력 표준화

모델 원본 예시:

```json
{"label":"FALL","score":0.94,"timestamp":"2026-08-09T09:00:00Z","sequence":7}
```

표준 관측으로 변환한다.

```powershell
python -m gateway.model_bridge `
  --input model-output.jsonl `
  --output inference.jsonl `
  --dead-letter model_bridge.dead.jsonl `
  --room-id room-03 `
  --device-id csi-gateway-a-01 `
  --model-version care-csi-1.0.0
```

실제 장기 실행 모델은 `gateway.model_bridge.normalize`를 호출해 추론 직후 표준 관측 한 줄을 `inference.jsonl`에 추가한다. 잘못된 행은 dead-letter로 분리한다.

## 5. 게이트웨이 실행

장치 키는 환경변수 또는 로컬 비밀 저장소에서 읽는다.

```powershell
python -m gateway.uploader `
  --api-url http://127.0.0.1:8000 `
  --device-key $env:CARE_SIGNAL_DEVICE_KEY `
  --input inference.jsonl `
  --spool pending_uploads.jsonl `
  --dead-letter gateway.dead.jsonl
```

검증 항목:

- 정상 관측이 병동 맵에 반영된다.
- 서버 중단 중 관측은 spool에 남는다.
- 서버 복구 후 오래된 관측부터 전송된다.
- 같은 `observation_id`를 다시 보내도 이력과 사건이 늘지 않는다.
- 잘못된 행이 있어도 다음 정상 관측이 처리된다.

## 6. 직원 병동 맵 시연

브라우저에서 `http://127.0.0.1:8000/`을 연다.

1. 직원 코드로 로그인한다.
2. 103호를 선택한다.
3. 장치 ID, 온라인 상태, 모델 버전, 처리 지연을 확인한다.
4. `IN_BED` 관측에서 초록 정상 상태를 확인한다.
5. `OUT_OF_BED` 관측에서 주황 주의 사건을 확인한다.
6. `MOVEMENT_ANOMALY` 관측에서 빨강 위험 사건을 확인한다.
7. `확인`을 눌러 장치 경보 중지를 확인한다.
8. `이동 중`, `처리 완료` 순서로 대응 기록을 남긴다.
9. 관측 이력과 사건 발생·확인·완료 시각을 확인한다.

## 7. 보호자 앱 시연

```powershell
cd mobile
pnpm install
pnpm typecheck
pnpm start
```

검증 항목:

- 연결 코드 로그인
- 안정·병동 알림·직원 확인 중·확인 완료 문구
- 모델 신뢰도와 임상 세부정보가 노출되지 않음
- 네트워크 단절 후 재시도
- 세션 만료 후 로그인 복귀
- 실제 기기에서 푸시 등록과 알림 수신

## 8. UNO R4 시연

`hardware/uno_r4_alert/config.example.h`를 `config.h`로 복사해 로컬 설정을 입력하고 Arduino IDE에서 UNO R4 WiFi 대상으로 컴파일한다.

- WARNING: `GENTLE_ONCE`
- CRITICAL: `URGENT_REPEAT`
- 직원 확인 또는 API 오류: 즉시 정지
- Wi-Fi 단절: 안전 정지 후 재연결

실제 부저 회로는 트랜지스터·저항과 부하 정격을 확인한 뒤 연결한다.

## 9. 모델 성능 평가

평가 JSONL의 각 행에 `ground_truth`, `state`를 넣고 가능하면 `captured_at`, `received_at`도 포함한다.

```powershell
python -m tools.evaluate_model evaluation.jsonl --output evaluation-report.json
```

보고서에 최소한 다음 값을 포함한다.

- 전체 정확도와 macro F1
- 상태별 precision, recall, F1, support
- 혼동행렬
- 수집부터 서버 수신까지 median, p95, 최대 지연
- 위험 상태 오탐과 미탐 사례 분석

## 10. 시연 전 최종 점검

- [ ] 전체 자동 테스트 통과
- [ ] 실제 비밀값이 Git 상태에 없음
- [ ] 공개 `/health`가 기대 DB를 표시
- [ ] 6개 병실 맵과 센서 단절 표시 정상
- [ ] 모델 관측 중복 방지 확인
- [ ] 사건 확인 후 UNO 소리 중지
- [ ] 보호자 정보 최소화 확인
- [ ] 실제 시연과 동일한 백업 영상 준비
- [ ] 네트워크 장애 시 사용할 시뮬레이터 준비
- [ ] 정확도·오탐률·지연 수치 발표 자료 반영
