#pragma once
// This public, unprovisioned profile compiles but refuses to start. Generate
// device_config.h privately using configure.py; do not commit that file/binary.
#define UE_WIFI_SSID ""
#define UE_WIFI_PASSWORD ""
#define UE_ENDPOINT ""
#define UE_DEVICE_ID ""
#define UE_DEVICE_TOKEN ""
#define UE_CA_CERTIFICATE ""
#define UE_SNTP_SERVER "pool.ntp.org"
#define UE_WIRING_VERIFIED false
#define UE_GPIO_BCLK 26
#define UE_GPIO_WS 25
#define UE_GPIO_DATA_IN 33
#define UE_I2S_SAMPLE_LSB 8
#define UE_SAMPLE_RATE 16000
#define UE_FRAMES_PER_RECORDING 16000
#define UE_RECORDING_INTERVAL_MS 1000
#define UE_UPLOAD_INTERVAL_MS 1000
#define UE_MAX_FRAMES 16000
#define UE_QUEUE_CAPACITY 2
#define UE_MAX_CLOCK_AGE_MS 3600000
#define UE_CLOCK_JUMP_TOLERANCE_MS 250
#define UE_SNTP_UNCERTAINTY_BUDGET_MS 100
#define UE_RETRY_MAX_ATTEMPTS 8
#define UE_RETRY_BASE_MS 1000
#define UE_RETRY_CAP_MS 30000
#define UE_RETRY_AFTER_CAP_MS 300000
#define UE_HTTP_TIMEOUT_MS 10000
