// UrbanEcho PCM compatibility client: classic ESP32 + INMP441, Arduino-ESP32 3.3.8.
// Raw signed PCM16 LE, mono, 16 kHz. Existing ESP-IDF firmware is a separate option.
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP_I2S.h>
#include <atomic>
#include <math.h>
#include <stdarg.h>
#include <time.h>
#include <sys/time.h>
#include "cJSON.h"
#include "esp_random.h"
#include "esp_sntp.h"
#include "esp_timer.h"

#if __has_include("privateconfig.h")
#include "privateconfig.h"
#else
#include "config.example.h"
#endif

constexpr unsigned SAMPLE_RATE = 16000;
constexpr unsigned RECORDING_SAMPLES = SAMPLE_RATE; // One second: backend interval must be 1.
constexpr unsigned RECORDING_BYTES = RECORDING_SAMPLES * sizeof(int16_t);
constexpr unsigned BUFFER_SLOTS = 2; // 64,000 bytes TOTAL: capture, pending, in-flight share pool.
constexpr int32_t FLAT_RANGE = 100;
constexpr int64_t CLOCK_MAX_AGE_US = 60LL * 60 * 1000000;
constexpr int64_t CLOCK_STEP_LIMIT_US = 250000;
constexpr unsigned STARTUP_DISCARD_FRAMES = SAMPLE_RATE * 400 / 1000;
static_assert(UE_CAPTURE_INTERVAL_MS >= 1000 && UE_CAPTURE_INTERVAL_MS <= 3600000,
              "Capture interval must be 1 second to 1 hour");
static_assert(UE_UPLOAD_INTERVAL_MS <= 3600000, "Upload interval must be <= 1 hour");
static_assert(UE_MAX_UPLOAD_ATTEMPTS >= 1 && UE_MAX_UPLOAD_ATTEMPTS <= 10, "Use 1..10 attempts");
static_assert(UE_HTTP_TIMEOUT_MS >= 1000 && UE_HTTP_TIMEOUT_MS <= 30000, "Use 1..30 second timeout");
static_assert(UE_GAIN > 0 && UE_GAIN <= 32, "Gain must be > 0 and <= 32");
static_assert(UE_PIN_BCLK == 26 && UE_PIN_WS == 25 && UE_PIN_DIN == 33,
              "This reviewed profile uses confirmed GPIO26/25/33 wiring");

struct AudioSlot {
  int16_t samples[RECORDING_SAMPLES];
  uint64_t seq;
  char capturedAt[32];
  unsigned rms;
  unsigned peak;
  unsigned clipped;
};
struct StatusMsg { char text[192]; };
struct ClockSnapshot { bool valid; int64_t offsetUs; };
struct DmaSnapshot { int64_t originUs; uint32_t overflows; };
enum MicState { MIC_UNKNOWN, MIC_OK, MIC_SILENT, MIC_CLIPPING };

I2SClass i2s;
AudioSlot buffers[BUFFER_SLOTS];
QueueHandle_t freeQ = nullptr, readyQ = nullptr, statusQ = nullptr;
char sessionId[33]{};
std::atomic<bool> fatalUpload{false};
std::atomic<unsigned> droppedCapture{0}, droppedUpload{0}, droppedStatus{0};
portMUX_TYPE stateLock = portMUX_INITIALIZER_UNLOCKED;
int64_t lastSyncUs = 0;
int64_t dmaOriginUs = 0;
uint32_t dmaOverflows = 0;

String baseUrl() {
  String url = String(UE_USE_HTTPS ? "https://" : "http://") + UE_HOST;
  if ((UE_USE_HTTPS && UE_PORT != 443) || (!UE_USE_HTTPS && UE_PORT != 80))
    url += ":" + String(UE_PORT);
  return url;
}

bool validUuid(const char* value) {
  size_t n = strlen(value);
  if (n != 32 && n != 36) return false;
  for (size_t i = 0; i < n; ++i) {
    if (n == 36 && (i == 8 || i == 13 || i == 18 || i == 23)) {
      if (value[i] != '-') return false;
    } else if (!isxdigit(static_cast<unsigned char>(value[i]))) return false;
  }
  return true;
}

bool validDeviceId(const char* value) {
  size_t length = strlen(value);
  if (length == 36) return validUuid(value);
  if (!length || length > 32) return false;
  for (size_t i = 0; i < length; ++i) {
    unsigned char ch = static_cast<unsigned char>(value[i]);
    if (!isalnum(ch) && ch != '_' && ch != '-') return false;
  }
  return true;
}

void postStatus(const char* fmt, ...) {
  StatusMsg msg{};
  va_list args;
  va_start(args, fmt);
  vsnprintf(msg.text, sizeof(msg.text), fmt, args);
  va_end(args);
  Serial.printf("STATUS: %s\n", msg.text);
  if (!statusQ || xQueueSend(statusQ, &msg, 0) != pdTRUE) ++droppedStatus;
}

void onClockSync(struct timeval*) {
  portENTER_CRITICAL(&stateLock);
  lastSyncUs = esp_timer_get_time();
  portEXIT_CRITICAL(&stateLock);
}

ClockSnapshot clockSnapshot() {
  timeval now{};
  gettimeofday(&now, nullptr);
  int64_t mono = esp_timer_get_time();
  portENTER_CRITICAL(&stateLock);
  int64_t synced = lastSyncUs;
  portEXIT_CRITICAL(&stateLock);
  // Epoch plausibility alone is insufficient: a successful recent SNTP update is required.
  return {synced > 0 && mono - synced < CLOCK_MAX_AGE_US && now.tv_sec >= 1704067200,
          int64_t(now.tv_sec) * 1000000 + now.tv_usec - mono};
}

bool IRAM_ATTR receivedDma(i2s_chan_handle_t, i2s_event_data_t* event, void*) {
  int64_t endedUs = esp_timer_get_time();
  portENTER_CRITICAL_ISR(&stateLock);
  if (!dmaOriginUs) {
    // Mono 32-bit DMA: one sample per 4 bytes. Wire clocks still carry both I2S slots.
    uint64_t frames = event->size / sizeof(int32_t);
    dmaOriginUs = endedUs - int64_t(frames * 1000000ULL / SAMPLE_RATE);
  }
  portEXIT_CRITICAL_ISR(&stateLock);
  return false;
}

bool IRAM_ATTR overflowDma(i2s_chan_handle_t, i2s_event_data_t*, void*) {
  portENTER_CRITICAL_ISR(&stateLock);
  ++dmaOverflows;
  portEXIT_CRITICAL_ISR(&stateLock);
  return false;
}

DmaSnapshot dmaSnapshot() {
  portENTER_CRITICAL(&stateLock);
  DmaSnapshot state{dmaOriginUs, dmaOverflows};
  portEXIT_CRITICAL(&stateLock);
  return state;
}

bool startI2S() {
  i2s.setPins(UE_PIN_BCLK, UE_PIN_WS, -1, UE_PIN_DIN);
  if (!i2s.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                 I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) return false;
  // The Arduino wrapper starts RX; briefly stop it to attach overflow/timing callbacks.
  auto rx = i2s.rxChan();
  if (i2s_channel_disable(rx) != ESP_OK) { i2s.end(); return false; }
  portENTER_CRITICAL(&stateLock);
  dmaOriginUs = 0;
  dmaOverflows = 0;
  portEXIT_CRITICAL(&stateLock);
  i2s_event_callbacks_t callbacks{};
  callbacks.on_recv = receivedDma;
  callbacks.on_recv_q_ovf = overflowDma;
  if (i2s_channel_register_event_callback(rx, &callbacks, nullptr) != ESP_OK ||
      i2s_channel_enable(rx) != ESP_OK) { i2s.end(); return false; }
  return true;
}

bool readRaw(int32_t* samples, size_t wanted, size_t& received) {
  size_t bytes = 0;
  auto result = i2s_channel_read(i2s.rxChan(), samples, wanted * sizeof(int32_t), &bytes, 100);
  received = bytes / sizeof(int32_t);
  return result == ESP_OK && bytes > 0 && bytes % sizeof(int32_t) == 0;
}

bool formatUtc(int64_t utcUs, char* output, size_t size) {
  time_t seconds = time_t(utcUs / 1000000);
  tm utc{};
  if (!gmtime_r(&seconds, &utc)) return false;
  char whole[24]{};
  if (!strftime(whole, sizeof(whole), "%Y-%m-%dT%H:%M:%S", &utc)) return false;
  return snprintf(output, size, "%s.%06ldZ", whole, long(utcUs % 1000000)) < int(size);
}

bool beginRequest(HTTPClient& http, WiFiClient& plain, WiFiClientSecure& secure, const char* route) {
  if (UE_USE_HTTPS) {
    secure.setCACert(UE_CA_CERT);
    secure.setHandshakeTimeout((UE_HTTP_TIMEOUT_MS + 999) / 1000);
  }
  String url = baseUrl() + route;
  bool began = UE_USE_HTTPS ? http.begin(secure, url) : http.begin(plain, url);
  if (!began) return false;
  http.setConnectTimeout(5000);
  http.setTimeout(UE_HTTP_TIMEOUT_MS);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  http.setReuse(false);
  http.addHeader("Authorization", String("Bearer ") + UE_DEVICE_TOKEN);
  http.addHeader("X-Device-Id", UE_DEVICE_ID);
  return true;
}

int sendText(const char* message) {
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  if (!beginRequest(http, plain, secure, "/text")) return -100;
  // Status is expendable: do not let its timeout consume an audio-sized retry budget.
  http.setConnectTimeout(500);
  http.setTimeout(500);
  if (UE_USE_HTTPS) secure.setHandshakeTimeout(1);
  http.addHeader("Content-Type", "text/plain; charset=utf-8");
  int code = http.POST(reinterpret_cast<uint8_t*>(const_cast<char*>(message)), strlen(message));
  http.end();
  return code;
}

bool parseAck(const String& body, uint64_t seq, char* id, size_t idSize) {
  cJSON* json = cJSON_Parse(body.c_str());
  if (!json) return false;
  auto* ok = cJSON_GetObjectItemCaseSensitive(json, "ok");
  auto* received = cJSON_GetObjectItemCaseSensitive(json, "seq");
  auto* saved = cJSON_GetObjectItemCaseSensitive(json, "id");
  auto* status = cJSON_GetObjectItemCaseSensitive(json, "status");
  auto* duplicate = cJSON_GetObjectItemCaseSensitive(json, "duplicate");
  bool knownStatus = cJSON_IsString(status) &&
    (!strcmp(status->valuestring, "pending") || !strcmp(status->valuestring, "processing") ||
     !strcmp(status->valuestring, "completed") || !strcmp(status->valuestring, "failed"));
  bool valid = cJSON_IsTrue(ok) && cJSON_IsNumber(received) && received->valuedouble == double(seq) &&
    cJSON_IsString(saved) && validUuid(saved->valuestring) && knownStatus && cJSON_IsBool(duplicate);
  if (valid) snprintf(id, idSize, "%s", saved->valuestring);
  cJSON_Delete(json);
  return valid;
}

void captureTask(void*) {
  static int32_t raw[256];
  uint64_t seq = 0;
  MicState lastState = MIC_UNKNOWN;
  unsigned stateRun = 0;
  while (true) {
    ClockSnapshot anchor = clockSnapshot();
    if (fatalUpload.load() || !anchor.valid) { vTaskDelay(pdMS_TO_TICKS(250)); continue; }
    if (!startI2S()) { postStatus("MIC ERROR: I2S initialization failed"); vTaskDelay(pdMS_TO_TICKS(2000)); continue; }
    uint64_t framesRead = 0;
    float prevX = 0, prevY = 0;
    bool healthy = true;
    // Discard startup transients. Maintain the same sample clock across every recording.
    while (framesRead < STARTUP_DISCARD_FRAMES) {
      size_t count = 0;
      size_t want = min(size_t(256), size_t(STARTUP_DISCARD_FRAMES - framesRead));
      if (!readRaw(raw, want, count)) { healthy = false; break; }
      framesRead += count;
    }
    while (healthy && !fatalUpload.load()) {
      ClockSnapshot now = clockSnapshot();
      if (!now.valid || llabs(now.offsetUs - anchor.offsetUs) > CLOCK_STEP_LIMIT_US || dmaSnapshot().overflows) break;
      uint8_t slot = 0;
      bool haveSlot = xQueueReceive(freeQ, &slot, 0) == pdTRUE;
      AudioSlot* record = haveSlot ? &buffers[slot] : nullptr;
      const uint64_t recordSeq = seq++;
      // cJSON represents numeric ACK sequence values as doubles: retain exact integers.
      if (recordSeq >= 9007199254740991ULL) { fatalUpload.store(true); healthy = false; }
      int64_t startMonoUs = dmaSnapshot().originUs + int64_t(framesRead * 1000000ULL / SAMPLE_RATE);
      if (record) {
        record->seq = recordSeq;
        healthy = healthy && formatUtc(startMonoUs + anchor.offsetUs, record->capturedAt, sizeof(record->capturedAt));
      }
      size_t filled = 0;
      double sumSq = 0;
      unsigned peak = 0, clipped = 0, rawClipped = 0;
      int32_t mn = INT32_MAX, mx = INT32_MIN;
      while (healthy && filled < RECORDING_SAMPLES && !fatalUpload.load()) {
        size_t count = 0;
        if (!readRaw(raw, min(size_t(256), size_t(RECORDING_SAMPLES - filled)), count)) { healthy = false; break; }
        for (size_t i = 0; i < count; ++i) {
          int32_t v24 = raw[i] >> 8;
          mn = min(mn, v24); mx = max(mx, v24);
          if (abs(v24) > 7500000) ++rawClipped;
          float x = float(v24);
          float y = x - prevX + 0.995f * prevY;
          prevX = x; prevY = y;
          float out = y * (UE_GAIN / 256.0f);
          if (out >= 32767.0f || out <= -32768.0f) ++clipped;
          out = max(-32768.0f, min(32767.0f, out));
          int16_t pcm = int16_t(out);
          if (record) record->samples[filled + i] = pcm;
          sumSq += double(pcm) * pcm;
          peak = max(peak, unsigned(abs(int(pcm))));
        }
        filled += count; framesRead += count;
        if (dmaSnapshot().overflows) healthy = false;
      }
      now = clockSnapshot();
      int64_t expectedEndUs = startMonoUs + 1000000;
      int64_t lagUs = esp_timer_get_time() - expectedEndUs;
      healthy = healthy && filled == RECORDING_SAMPLES && !fatalUpload.load() && now.valid &&
        llabs(now.offsetUs - anchor.offsetUs) <= CLOCK_STEP_LIMIT_US && lagUs >= -50000 && lagUs <= 250000;
      if (!healthy) {
        if (haveSlot) xQueueSend(freeQ, &slot, 0);
        ++droppedCapture;
        break;
      }
      if (record) {
        record->rms = unsigned(sqrt(sumSq / RECORDING_SAMPLES));
        record->peak = peak;
        record->clipped = clipped;
        if (xQueueSend(readyQ, &slot, 0) != pdTRUE) { ++droppedCapture; xQueueSend(freeQ, &slot, 0); }
      } else ++droppedCapture;
      MicState state = (rawClipped > RECORDING_SAMPLES / 10 || clipped > 0) ? MIC_CLIPPING :
                       ((mx - mn) < FLAT_RANGE ? MIC_SILENT : MIC_OK);
      if (state == lastState) ++stateRun; else { lastState = state; stateRun = 1; }
      if (stateRun == (state == MIC_OK ? 2U : 3U)) {
        if (state == MIC_OK) postStatus("MIC OK: signal present; PCM16 gain %.1f, UNCALIBRATED", double(UE_GAIN));
        else if (state == MIC_SILENT) postStatus("MIC SILENT: check SD33 WS25 BCLK26 L/R=GND and 3.3V");
        else postStatus("MIC CLIPPING: inspect gain %.1f, loud input and wiring; not a calibrated sound level", double(UE_GAIN));
      }
      // Keep draining DMA during a configured sampling gap so the next recording is fresh.
      uint64_t gapFrames = uint64_t(UE_CAPTURE_INTERVAL_MS - 1000) * SAMPLE_RATE / 1000;
      while (healthy && gapFrames && !fatalUpload.load()) {
        size_t count = 0;
        if (!readRaw(raw, min(size_t(256), size_t(gapFrames)), count)) { healthy = false; break; }
        gapFrames -= count; framesRead += count;
        now = clockSnapshot();
        if (dmaSnapshot().overflows || !now.valid || llabs(now.offsetUs - anchor.offsetUs) > CLOCK_STEP_LIMIT_US) healthy = false;
      }
      if (UE_CAPTURE_INTERVAL_MS > 1000) { prevX = 0; prevY = 0; }
    }
    i2s.end();
    postStatus("CAPTURE GAP: clock/I2S/backpressure recovery; dropped_capture=%u", droppedCapture.load());
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

void uploadTask(void*) {
  uint32_t lastStatusMs = 0, lastStatsMs = 0, lastWifiAttemptMs = 0;
  int64_t nextUploadUs = 0;
  while (true) {
    if (fatalUpload.load()) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
    if (WiFi.status() != WL_CONNECTED && millis() - lastWifiAttemptMs > 5000) {
      WiFi.reconnect(); lastWifiAttemptMs = millis();
    }
    uint8_t slot = 0;
    if (xQueueReceive(readyQ, &slot, pdMS_TO_TICKS(100)) == pdTRUE) {
      AudioSlot& c = buffers[slot];
      bool saved = false;
      while (esp_timer_get_time() < nextUploadUs) vTaskDelay(pdMS_TO_TICKS(25));
      for (unsigned attempt = 0; attempt < UE_MAX_UPLOAD_ATTEMPTS && !saved; ++attempt) {
        int code = -1;
        char savedId[37]{};
        if (WiFi.status() == WL_CONNECTED && clockSnapshot().valid) {
          WiFiClientSecure secure;
          WiFiClient plain;
          HTTPClient http;
          if (beginRequest(http, plain, secure, "/upload")) {
            char sequence[24]{};
            snprintf(sequence, sizeof(sequence), "%llu", (unsigned long long)c.seq);
            http.addHeader("Content-Type", "application/octet-stream");
            http.addHeader("X-Session", sessionId);
            http.addHeader("X-Seq", sequence);
            http.addHeader("X-Captured-At", c.capturedAt);
            code = http.POST(reinterpret_cast<uint8_t*>(c.samples), RECORDING_BYTES);
            // FastAPI sends a bounded Content-Length JSON ACK; never consume arbitrary response bodies.
            if (code == 200 && http.getSize() > 0 && http.getSize() <= 1024)
              saved = parseAck(http.getString(), c.seq, savedId, sizeof(savedId));
            http.end();
          }
        } else WiFi.reconnect();
        Serial.printf("seq=%llu http=%d attempt=%u rms=%u peak=%u clipped=%u%s%s\n",
          (unsigned long long)c.seq, code, attempt + 1, c.rms, c.peak, c.clipped,
          saved ? " saved_audio_id=" : "", saved ? savedId : "");
        if (code >= 400 && code < 500 && code != 408 && code != 429) {
          postStatus("UPLOAD PAUSED: HTTP %d. Correct identity/token/format/time before rebooting.", code);
          fatalUpload.store(true);
          break;
        }
        if (!saved && attempt + 1 < UE_MAX_UPLOAD_ATTEMPTS)
          vTaskDelay(pdMS_TO_TICKS(1000U << min(attempt, 3U)));
      }
      if (!saved) ++droppedUpload;
      xQueueSend(freeQ, &slot, 0);
      nextUploadUs = esp_timer_get_time() + int64_t(UE_UPLOAD_INTERVAL_MS) * 1000;
      if (fatalUpload.load()) {
        while (xQueueReceive(readyQ, &slot, 0) == pdTRUE) { ++droppedUpload; xQueueSend(freeQ, &slot, 0); }
      }
    }
    if (millis() - lastStatsMs >= 10000) {
      postStatus("BUFFER: queued=%u dropped_capture=%u dropped_upload=%u dropped_status=%u heap=%u",
        unsigned(uxQueueMessagesWaiting(readyQ)), droppedCapture.load(), droppedUpload.load(), droppedStatus.load(), ESP.getFreeHeap());
      lastStatsMs = millis();
    }
    // Best-effort diagnostics cannot indefinitely starve audio. Serial remains the authoritative drop counter.
    if (!fatalUpload.load() && uxQueueMessagesWaiting(readyQ) == 0 &&
        WiFi.status() == WL_CONNECTED && clockSnapshot().valid && millis() - lastStatusMs >= 10000) {
      StatusMsg msg{};
      if (xQueueReceive(statusQ, &msg, 0) == pdTRUE) Serial.printf("status http=%d\n", sendText(msg.text));
      lastStatusMs = millis();
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  bool configured = UE_CONFIGURED && validDeviceId(UE_DEVICE_ID) && strlen(UE_DEVICE_TOKEN) >= 16 &&
    strlen(UE_WIFI_SSID) > 0 && !strstr(UE_HOST, "YOUR_") && !strstr(UE_HOST, "://") && !strchr(UE_HOST, '/') &&
    (UE_USE_HTTPS ? strncmp(UE_CA_CERT, "-----BEGIN CERTIFICATE-----", 27) == 0 && !strstr(UE_CA_CERT, "PASTE_") : UE_ALLOW_HTTP_BENCH);
  if (!configured) {
    Serial.println("Not provisioned. Copy config.example.h to privateconfig.h and complete the private settings.");
    while (true) delay(1000);
  }
  freeQ = xQueueCreate(BUFFER_SLOTS, sizeof(uint8_t));
  readyQ = xQueueCreate(BUFFER_SLOTS, sizeof(uint8_t));
  statusQ = xQueueCreate(8, sizeof(StatusMsg));
  if (!freeQ || !readyQ || !statusQ) {
    Serial.println("FATAL: insufficient memory for queues");
    while (true) delay(1000);
  }
  for (uint8_t i = 0; i < BUFFER_SLOTS; ++i) xQueueSend(freeQ, &i, 0);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  WiFi.begin(UE_WIFI_SSID, UE_WIFI_PASSWORD);
  uint8_t nonce[16];
  esp_fill_random(nonce, sizeof(nonce)); // Wi-Fi entropy is active; session never changes during retries.
  for (unsigned i = 0; i < sizeof(nonce); ++i) snprintf(sessionId + i * 2, 3, "%02x", nonce[i]);
  sntp_set_time_sync_notification_cb(onClockSync);
  configTime(0, 0, UE_SNTP_SERVER, UE_SNTP_BACKUP);
  esp_sntp_set_sync_interval(15UL * 60 * 1000);
  postStatus("BOOT: %s; session=%s; PCM16 mono 16000Hz 1s gain %.1f UNCALIBRATED", UE_DEVICE_ID, sessionId, double(UE_GAIN));
  Serial.printf("Audio endpoint: %s/upload. Waiting for Wi-Fi and synchronized UTC.\n", baseUrl().c_str());
  if (xTaskCreatePinnedToCore(captureTask, "capture", 6144, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(uploadTask, "upload", 12288, nullptr, 2, nullptr, 0) != pdPASS) {
    fatalUpload.store(true);
    Serial.println("FATAL: insufficient memory for capture/upload tasks");
  }
}

void loop() {
  // Queue bounded serial diagnostics rather than opening a competing HTTP connection.
  static char line[128]{};
  static size_t length = 0;
  while (Serial.available()) {
    char ch = char(Serial.read());
    if (ch == '\n') { line[length] = '\0'; if (length) postStatus("%s", line); length = 0; }
    else if (ch != '\r' && length + 1 < sizeof(line)) line[length++] = ch;
  }
  vTaskDelay(pdMS_TO_TICKS(20));
}
