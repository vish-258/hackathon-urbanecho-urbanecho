#pragma once

// Copy to privateconfig.h beside UrbanEchoPCM.ino; edit the private copy only.
// Do not publish privateconfig.h or a firmware binary built with real secrets.
constexpr bool UE_CONFIGURED = false; // Set true after completing every field.
// Set true to reuse Wi-Fi already stored on this board; no password extraction.
// Preserve its NVS partition when flashing. Otherwise supply Wi-Fi below.
#define UE_USE_SAVED_WIFI false
constexpr char UE_WIFI_SSID[] = "YOUR_2_4_GHZ_WIFI";
constexpr char UE_WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char UE_HOST[] = "YOUR_SERVER_LAN_IP_OR_HOSTNAME"; // No scheme/path.
constexpr bool UE_USE_HTTPS = true;
constexpr unsigned short UE_PORT = 8443;
// Explicit opt-in for an isolated bench LAN only; credentials are cleartext on HTTP.
constexpr bool UE_ALLOW_HTTP_BENCH = false;

// The board always identifies itself as ESP-<12 uppercase factory MAC hex digits>.
// Leave the token empty to provision it over USB with scripts/provision-board.py;
// each board keeps its own token in NVS. A MAC is an ID, not a secret or credential.
// A private compiled token is supported only for that board's matching registration.
constexpr char UE_DEVICE_TOKEN[] = "";
constexpr char UE_CA_CERT[] = R"PEM(-----BEGIN CERTIFICATE-----
PASTE_SERVER_CA_CERTIFICATE_HERE
-----END CERTIFICATE-----
)PEM";

constexpr char UE_SNTP_SERVER[] = "pool.ntp.org";
constexpr char UE_SNTP_BACKUP[] = "time.cloudflare.com";
constexpr unsigned UE_CAPTURE_INTERVAL_MS = 1000; // Continuous 1-second uploads; server joins incident audio.
constexpr unsigned UE_UPLOAD_INTERVAL_MS = 0; // Minimum pause between recordings' uploads.
constexpr unsigned UE_HTTP_TIMEOUT_MS = 10000;
constexpr unsigned UE_MAX_UPLOAD_ATTEMPTS = 6;
constexpr float UE_GAIN = 16.0f; // Preserves prototype processing; NOT acoustic calibration.

constexpr int UE_PIN_BCLK = 26;
constexpr int UE_PIN_WS = 25;
constexpr int UE_PIN_DIN = 33;
