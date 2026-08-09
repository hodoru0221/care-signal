#include <Arduino.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include <WiFiS3.h>

#include "AlertLogic.h"
#include "config.h"

using care_signal::AlertAction;
using care_signal::AlertLogic;
using care_signal::AlertSnapshot;
using care_signal::SoundPattern;

namespace {
constexpr uint32_t kPollMs = 1000;
constexpr uint32_t kWifiRetryMs = 5000;
constexpr uint32_t kHttpTimeoutMs = 4000;

WiFiSSLClient tls;
HttpClient http(tls, API_HOST, API_PORT);
AlertLogic logic;
uint32_t last_poll_ms = 0;
uint32_t last_wifi_attempt_ms = 0;

String urlEncode(const char* value) {
  const char hex[] = "0123456789ABCDEF";
  String result;
  while (*value) {
    const unsigned char c = static_cast<unsigned char>(*value++);
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') {
      result += static_cast<char>(c);
    } else {
      result += '%';
      result += hex[c >> 4];
      result += hex[c & 0x0F];
    }
  }
  return result;
}

void playAction(AlertAction action) {
  if (action == AlertAction::STOP_SOUND) {
    noTone(BUZZER_PIN);
  } else if (action == AlertAction::PLAY_GENTLE) {
    tone(BUZZER_PIN, 880, 160);
    delay(220);
    tone(BUZZER_PIN, 1047, 160);
  } else if (action == AlertAction::PLAY_URGENT) {
    for (int i = 0; i < 3; ++i) {
      tone(BUZZER_PIN, 1400, 120);
      delay(170);
    }
  }
}

SoundPattern parsePattern(const char* pattern) {
  if (strcmp(pattern, "GENTLE_ONCE") == 0) return SoundPattern::GENTLE_ONCE;
  if (strcmp(pattern, "URGENT_REPEAT") == 0) return SoundPattern::URGENT_REPEAT;
  return SoundPattern::NORMAL;
}

void stopOnError(const __FlashStringHelper* message) {
  Serial.println(message);
  http.stop();
  playAction(logic.failSafe());
}

void pollAlert() {
  String path = String("/api/v1/devices/") + urlEncode(DEVICE_ID) +
                "/alert?room_id=" + urlEncode(ROOM_ID) +
                "&location=" + urlEncode(DEVICE_LOCATION);
  http.setHttpResponseTimeout(kHttpTimeoutMs);
  http.beginRequest();
  http.get(path);
  http.sendHeader("Accept", "application/json");
  if (strlen(API_BEARER_TOKEN) > 0) {
    http.sendHeader("Authorization", String("Bearer ") + API_BEARER_TOKEN);
  }
  http.endRequest();

  const int status = http.responseStatusCode();
  if (status != 200) {
    stopOnError(F("Alert HTTP request failed; buzzer stopped"));
    return;
  }

  const String body = http.responseBody();
  JsonDocument doc;
  if (deserializeJson(doc, body) != DeserializationError::Ok ||
      !doc["level"].is<const char*>() || !doc["sound"].is<bool>()) {
    stopOnError(F("Invalid alert JSON; buzzer stopped"));
    return;
  }

  const bool sound = doc["sound"].as<bool>();
  const char* pattern_text = doc["sound_pattern"] | "NORMAL";
  const char* event_id = doc["event_id"] | "";
  const SoundPattern pattern = parsePattern(pattern_text);
  if (sound && (pattern == SoundPattern::NORMAL || strlen(event_id) == 0)) {
    stopOnError(F("Unsafe alert fields; buzzer stopped"));
    return;
  }

  playAction(logic.apply({sound, pattern, event_id}, millis()));
  http.stop();
}

void maintainWifi(uint32_t now_ms) {
  if (WiFi.status() == WL_CONNECTED) return;
  playAction(logic.failSafe());
  if (static_cast<uint32_t>(now_ms - last_wifi_attempt_ms) < kWifiRetryMs) return;
  last_wifi_attempt_ms = now_ms;
  Serial.println(F("Connecting to Wi-Fi..."));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}
}  // namespace

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  noTone(BUZZER_PIN);
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  last_wifi_attempt_ms = millis();
}

void loop() {
  const uint32_t now_ms = millis();
  maintainWifi(now_ms);
  if (WiFi.status() != WL_CONNECTED) return;

  playAction(logic.tick(now_ms));
  if (static_cast<uint32_t>(now_ms - last_poll_ms) >= kPollMs) {
    last_poll_ms = now_ms;
    pollAlert();
  }
}
