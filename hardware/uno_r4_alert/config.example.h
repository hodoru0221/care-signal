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

// Leave empty when the endpoint does not require authentication.
#define API_BEARER_TOKEN ""

// Connect a passive buzzer between this digital pin and GND.
#define BUZZER_PIN 8
