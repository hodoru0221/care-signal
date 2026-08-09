#pragma once

// Copy to config.h and fill locally. config.h is ignored by this folder's
// .gitignore, so Wi-Fi credentials and tokens are never committed.
#define WIFI_SSID "replace-me"
#define WIFI_PASSWORD "replace-me"

// Host only: no https:// prefix and no trailing path.
#define API_HOST "care-signal.example.com"
#define API_PORT 443
#define DEVICE_ID "uno-room-01"
#define ROOM_ID "room-01"
#define DEVICE_LOCATION "room"

// Use the same device key configured on the API server. Keep it only in config.h.
#define DEVICE_API_KEY "replace-with-device-key"

// Connect a passive buzzer between this digital pin and GND.
#define BUZZER_PIN 8
