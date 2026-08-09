# Care Signal UNO R4 WiFi alert

병실용 수동 부저 알림기의 기본 구현이다. Arduino UNO R4 WiFi가 HTTPS로 아래 API를 1초마다 조회한다.

```text
GET /api/v1/devices/{device_id}/alert?room_id=...&location=room
```

응답의 `level`, `sound`, `sound_pattern`, `event_id`를 검사한다. `GENTLE_ONCE`는 같은 이벤트에서 한 번만 재생하고, `URGENT_REPEAT`는 1.5초 간격으로 반복한다. `NORMAL` 또는 `sound=false`이면 즉시 소리를 멈춘다. 서버는 직원이 이벤트를 `ACKNOWLEDGED` 처리한 뒤 해당 이벤트를 활성 목록에서 제외하므로 다음 폴링 응답이 `NORMAL/sound=false`가 되고 부저가 정지한다.

## 준비

Arduino IDE Library Manager에서 다음 라이브러리를 설치한다.

- `WiFiS3` (UNO R4 WiFi 보드 패키지)
- `ArduinoHttpClient`
- `ArduinoJson` 7.x

`config.example.h`를 `config.h`로 복사해 Wi-Fi, 서버와 `DEVICE_API_KEY`를 입력한다. `config.h`는 이 폴더의 `.gitignore`에 포함되어 있다. 실제 장치 키를 스케치나 `config.example.h`에 넣지 않는다. 요청은 `X-Device-Key` 헤더로 인증한다. 서버 인증서가 UNO R4 WiFi 펌웨어/루트 인증서 저장소에서 신뢰 가능해야 HTTPS 연결이 성공한다.

수동 부저를 기본값인 D8과 GND 사이에 연결한다. 능동 부저나 큰 부하를 직접 구동하지 말고 적절한 트랜지스터/저항 회로를 사용한다.

## 오류 및 재연결

- Wi-Fi가 끊기면 부저를 안전하게 정지하고 5초마다 재연결한다.
- HTTPS 상태가 200이 아니거나 타임아웃, JSON 파싱 실패, 필수 필드 누락, 모순된 경보 필드가 있으면 부저를 정지한다.
- 일시 오류 뒤 같은 `GENTLE_ONCE` 이벤트가 다시 와도 재생하지 않는다.
- `millis()` 오버플로를 고려한 unsigned 시간 차를 사용한다.

## 순수 로직 테스트

`AlertLogic.h/.cpp`는 Arduino API에 의존하지 않는 C++ 로직이다. C++11 이상 컴파일러가 있는 환경에서 다음처럼 실행한다.

```sh
g++ -std=c++11 -Wall -Wextra -pedantic AlertLogic.cpp tests/alert_logic_test.cpp -o alert_logic_test
./alert_logic_test
```

테스트는 NORMAL 정지, GENTLE_ONCE 중복 방지, 오류 시 정지, URGENT_REPEAT 주기, ACK 후 정지, `millis()` 오버플로를 다룬다.

## 검증 한계

현재 개발 환경에는 `arduino-cli`, C++ 호스트 컴파일러, 실제 UNO R4 WiFi 보드가 없다. 따라서 스케치와 순수 C++ 테스트는 작성 및 정적 검토만 했으며 Arduino 컴파일, 테스트 바이너리 실행, 실제 Wi-Fi/TLS/부저 동작은 검증하지 못했다. 보드 배포 전 Arduino IDE에서 `Arduino UNO R4 WiFi` 대상으로 컴파일하고 실기기에서 인증서, 재연결, 폴링 간격과 부저 회로를 확인해야 한다.
