#include "urbanecho/device_core.hpp"
#if __has_include("device_config.h")
#include "device_config.h"
#else
#include "device_config.example.h"
#endif
#include <atomic>
#include <cinttypes>
#include <cstring>
#include <new>
#include <utility>
#include <sys/time.h>
#include "cJSON.h"
#include "driver/i2s_std.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace {
constexpr char tag[] = "urbanecho";
constexpr size_t dma_frames = 128;
constexpr uint32_t startup_discard_ms = 400;
static_assert(UE_MAX_FRAMES <= 16000, "Verify ESP-WROOM RAM headroom before increasing fixed buffers");
static_assert(UE_QUEUE_CAPACITY == 2, "This ESP-WROOM profile budgets two fixed audio slots");
static_assert(UE_I2S_SAMPLE_LSB == 8, "Confirmed INMP441 Philips 32-bit slots carry signed high 24 bits");
static_assert(UE_FRAMES_PER_RECORDING <= UE_MAX_FRAMES, "Recording must fit its fixed audio buffer");
static_assert(UE_FRAMES_PER_RECORDING % UE_SAMPLE_RATE == 0,
              "Recording duration must match an integer-second backend location interval");
using Queue = urbanecho::CaptureQueue<UE_MAX_FRAMES, UE_QUEUE_CAPACITY>;
alignas(Queue) unsigned char queue_storage[sizeof(Queue)];
std::atomic<bool> wifi_connected{false};
Queue* captures = nullptr;

uint64_t monotonic_ms() { return uint64_t(esp_timer_get_time()) / 1000; }

struct TimeSync { int64_t utc_ms; uint64_t monotonic_ms; };
QueueHandle_t sync_queue = nullptr;
void time_sync_callback(struct timeval*) {
    struct timeval now{};
    gettimeofday(&now, nullptr);
    const TimeSync sync{int64_t(now.tv_sec) * 1000 + now.tv_usec / 1000, monotonic_ms()};
    if (sync_queue) xQueueOverwrite(sync_queue, &sync);
}

void wifi_event(void*, esp_event_base_t base, int32_t event, void*) {
    if (base == IP_EVENT && event == IP_EVENT_STA_GOT_IP) {
        wifi_connected.store(true);
        ESP_LOGI(tag, "Wi-Fi connected; waiting for usable UTC if not synchronized");
    } else if (base == WIFI_EVENT && event == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_connected.store(false);
        ESP_LOGW(tag, "Wi-Fi disconnected; bounded recording queue remains active");
    }
}

void initialize_wifi_and_time() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t initialization = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&initialization));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, nullptr));
    wifi_config_t wifi{};
    std::memcpy(wifi.sta.ssid, UE_WIFI_SSID, std::strlen(UE_WIFI_SSID));
    std::memcpy(wifi.sta.password, UE_WIFI_PASSWORD, std::strlen(UE_WIFI_PASSWORD));
    wifi.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi.sta.pmf_cfg.capable = true;
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Avoid modem power-save latency while servicing a continuous audio stream.
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, UE_SNTP_SERVER);
    esp_sntp_set_time_sync_notification_cb(time_sync_callback);
    esp_sntp_set_sync_interval(15 * 60 * 1000);
    esp_sntp_init();
}

urbanecho::BootIdentity next_boot_identity() {
    nvs_handle_t storage;
    ESP_ERROR_CHECK(nvs_open("urbanecho", NVS_READWRITE, &storage));
    uint64_t counter = 0;
    const auto result = nvs_get_u64(storage, "boot_count", &counter);
    if (result != ESP_OK && result != ESP_ERR_NVS_NOT_FOUND) ESP_ERROR_CHECK(result);
    if (counter == UINT64_MAX) ESP_ERROR_CHECK(ESP_ERR_INVALID_STATE);
    ESP_ERROR_CHECK(nvs_set_u64(storage, "boot_count", ++counter));
    ESP_ERROR_CHECK(nvs_commit(storage));
    nvs_close(storage);
    urbanecho::BootIdentity identity;
    identity.persisted_boot_counter = counter;
    // Wi-Fi has been started: ESP32 hardware RNG entropy source is enabled.
    esp_fill_random(identity.random_nonce.data(), identity.random_nonce.size());
    return identity;
}

struct DmaClock {
    portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
    int64_t origin_us = 0;
    uint32_t overflows = 0;
} dma_clock;

bool IRAM_ATTR received_dma(i2s_chan_handle_t, i2s_event_data_t* event, void*) {
    const int64_t ended_us = esp_timer_get_time();
    portENTER_CRITICAL_ISR(&dma_clock.lock);
    if (dma_clock.origin_us == 0) {
        const uint64_t frames = event->size / (2 * sizeof(uint32_t));
        dma_clock.origin_us = ended_us - int64_t(frames * 1000000ULL / UE_SAMPLE_RATE);
    }
    portEXIT_CRITICAL_ISR(&dma_clock.lock);
    return false;
}
bool IRAM_ATTR overflow_dma(i2s_chan_handle_t, i2s_event_data_t*, void*) {
    portENTER_CRITICAL_ISR(&dma_clock.lock);
    ++dma_clock.overflows;
    portEXIT_CRITICAL_ISR(&dma_clock.lock);
    return false;
}
std::pair<int64_t, uint32_t> dma_snapshot() {
    portENTER_CRITICAL(&dma_clock.lock);
    const auto snapshot = std::make_pair(dma_clock.origin_us, dma_clock.overflows);
    portEXIT_CRITICAL(&dma_clock.lock);
    return snapshot;
}
i2s_chan_handle_t start_microphone() {
    portENTER_CRITICAL(&dma_clock.lock);
    dma_clock.origin_us = 0;
    dma_clock.overflows = 0;
    portEXIT_CRITICAL(&dma_clock.lock);
    i2s_chan_config_t channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel.dma_desc_num = 8;
    channel.dma_frame_num = dma_frames;
    i2s_chan_handle_t rx = nullptr;
    ESP_ERROR_CHECK(i2s_new_channel(&channel, nullptr, &rx));
    i2s_std_config_t standard{};
    standard.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(UE_SAMPLE_RATE);
    standard.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
    standard.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    standard.gpio_cfg.mclk = I2S_GPIO_UNUSED;
    standard.gpio_cfg.bclk = gpio_num_t(UE_GPIO_BCLK);
    standard.gpio_cfg.ws = gpio_num_t(UE_GPIO_WS);
    standard.gpio_cfg.dout = I2S_GPIO_UNUSED;
    standard.gpio_cfg.din = gpio_num_t(UE_GPIO_DATA_IN);
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx, &standard));
    i2s_event_callbacks_t callbacks{};
    callbacks.on_recv = received_dma;
    callbacks.on_recv_q_ovf = overflow_dma;
    ESP_ERROR_CHECK(i2s_channel_register_event_callback(rx, &callbacks, nullptr));
    ESP_ERROR_CHECK(i2s_channel_enable(rx));
    return rx;
}

struct HttpWork { const urbanecho::MultipartRequest* request; std::string_view token; };
struct HttpResponseContext { uint32_t retry_after_ms = 0; };

esp_err_t http_event(esp_http_client_event_t* event) {
    if (event->event_id == HTTP_EVENT_ON_HEADER && event->header_key && event->header_value &&
        strcasecmp(event->header_key, "Retry-After") == 0) {
        char* end = nullptr;
        const unsigned long seconds = strtoul(event->header_value, &end, 10);
        if (end != event->header_value && *end == '\0')
            static_cast<HttpResponseContext*>(event->user_data)->retry_after_ms = uint32_t(std::min(seconds, 300UL) * 1000);
    }
    return ESP_OK;
}

bool valid_audio_ack(const char* body, int status, char (&saved_id)[37]) {
    cJSON* json = cJSON_Parse(body);
    if (!json) return false;
    const auto* id = cJSON_GetObjectItemCaseSensitive(json, "id");
    const auto* duplicate = cJSON_GetObjectItemCaseSensitive(json, "duplicate");
    const auto* processing = cJSON_GetObjectItemCaseSensitive(json, "status");
    const bool known_state = cJSON_IsString(processing) &&
        (std::strcmp(processing->valuestring, "pending") == 0 || std::strcmp(processing->valuestring, "processing") == 0 ||
         std::strcmp(processing->valuestring, "completed") == 0 || std::strcmp(processing->valuestring, "failed") == 0);
    const bool valid = cJSON_IsString(id) && urbanecho::uuid_valid(id->valuestring) && known_state &&
        ((status == 202 && cJSON_IsFalse(duplicate)) || (status == 200 && cJSON_IsTrue(duplicate)));
    if (valid) std::memcpy(saved_id, id->valuestring, 36);
    cJSON_Delete(json);
    return valid;
}

urbanecho::TransferResult perform_upload(HttpWork work) {
    urbanecho::TransferResult result{urbanecho::TransferState::finished, 0, false, 0};
    HttpResponseContext response_context;
    char url[320]{};
    char authorization[320]{};
    char content_type[128]{};
    char saved_id[37]{};
    std::snprintf(url, sizeof url, "%s/audio", UE_ENDPOINT);
    std::snprintf(authorization, sizeof authorization, "Bearer %.*s", int(work.token.size()), work.token.data());
    std::snprintf(content_type, sizeof content_type, "multipart/form-data; boundary=%.*s",
                  int(work.request->boundary().size()), work.request->boundary().data());
    esp_http_client_config_t config{};
    config.url = url;
    config.method = HTTP_METHOD_POST;
    config.cert_pem = UE_CA_CERTIFICATE;
    config.skip_cert_common_name_check = false;
    config.disable_auto_redirect = true;
    config.max_authorization_retries = -1;
    config.timeout_ms = UE_HTTP_TIMEOUT_MS;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.event_handler = http_event;
    config.user_data = &response_context;
    auto client = esp_http_client_init(&config);
    if (!client) return result;
    bool ok = esp_http_client_set_header(client, "Authorization", authorization) == ESP_OK &&
              esp_http_client_set_header(client, "Content-Type", content_type) == ESP_OK &&
              esp_http_client_open(client, int(work.request->content_length())) == ESP_OK;
    // Every write uses the original frozen buffer. No WAV-sized heap copy.
    const uint64_t deadline = monotonic_ms() + 30000;
    for (const auto& part : work.request->parts()) {
        for (size_t sent = 0; ok && sent < part.size;) {
            if (monotonic_ms() >= deadline) { ok = false; break; }
            const auto amount = std::min(size_t(1024), part.size - sent);
            const int written = esp_http_client_write(client, reinterpret_cast<const char*>(part.data + sent), int(amount));
            if (written <= 0) { ok = false; break; }
            sent += size_t(written);
        }
    }
    if (ok && esp_http_client_fetch_headers(client) >= 0) {
        result.http_status = esp_http_client_get_status_code(client);
        char response[1024]{};
        const int length = esp_http_client_read_response(client, response, sizeof response - 1);
        if (length >= 0 && length < int(sizeof response - 1) && esp_http_client_is_complete_data_received(client)) {
            response[length] = '\0';
            result.valid_audio_ack = valid_audio_ack(response, result.http_status, saved_id);
        }
        result.retry_after_ms = response_context.retry_after_ms;
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    std::memset(authorization, 0, sizeof authorization);
    // Report only status, never credentials, SSID, response body, or original audio.
    ESP_LOGI(tag, "Upload HTTP status=%d acknowledgement=%s", result.http_status,
             result.valid_audio_ack ? "accepted" : "not accepted");
    if (result.valid_audio_ack) ESP_LOGI(tag, "saved_audio_id=%s", saved_id);
    return result;
}

class EspTransport : public urbanecho::HttpTransport {
public:
    EspTransport() {
        work_ = xQueueCreate(1, sizeof(HttpWork));
        results_ = xQueueCreate(1, sizeof(urbanecho::TransferResult));
        if (!work_ || !results_ || xTaskCreate(network_task, "ue_https", 8192, this, 4, nullptr) != pdPASS)
            ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
    bool network_ready() const override { return wifi_connected.load(); }
    bool trust_ready() const override { return captures && captures->time_usable(monotonic_ms()); }
    bool begin(const urbanecho::MultipartRequest& request, std::string_view token) override {
        const HttpWork work{&request, token};
        return xQueueSend(work_, &work, 0) == pdTRUE;
    }
    urbanecho::TransferResult poll() override {
        urbanecho::TransferResult result;
        if (xQueueReceive(results_, &result, 0) != pdTRUE) return {};
        return result;
    }
private:
    static void network_task(void* argument) {
        auto* transport = static_cast<EspTransport*>(argument);
        for (;;) {
            HttpWork work;
            if (xQueueReceive(transport->work_, &work, portMAX_DELAY) == pdTRUE) {
                const auto result = perform_upload(work);
                xQueueSend(transport->results_, &result, portMAX_DELAY);
            }
        }
    }
    QueueHandle_t work_;
    QueueHandle_t results_;
};

bool configuration_ready() {
    const std::string_view token(UE_DEVICE_TOKEN);
    return UE_WIRING_VERIFIED && std::strlen(UE_WIFI_SSID) > 0 && std::strlen(UE_WIFI_SSID) <= 32 &&
           std::strlen(UE_WIFI_PASSWORD) >= 8 && std::strlen(UE_WIFI_PASSWORD) <= 63 &&
           urbanecho::uuid_valid(UE_DEVICE_ID) && token.size() >= 32 && token.size() < 300 &&
           token.find_first_of("\r\n") == std::string_view::npos &&
           std::strncmp(UE_ENDPOINT, "https://", 8) == 0 && std::strlen(UE_ENDPOINT) < 300 &&
           std::strstr(UE_CA_CERTIFICATE, "-----BEGIN CERTIFICATE-----") != nullptr;
}

urbanecho::Config capture_config() {
    urbanecho::Config config;
    config.sample_rate = UE_SAMPLE_RATE;
    config.frames_per_recording = UE_FRAMES_PER_RECORDING;
    config.recording_interval_ms = UE_RECORDING_INTERVAL_MS;
    config.upload_interval_ms = UE_UPLOAD_INTERVAL_MS;
    config.max_clock_age_ms = UE_MAX_CLOCK_AGE_MS;
    config.clock_jump_tolerance_ms = UE_CLOCK_JUMP_TOLERANCE_MS;
    config.max_sync_uncertainty_ms = UE_SNTP_UNCERTAINTY_BUDGET_MS;
    config.max_attempts = UE_RETRY_MAX_ATTEMPTS;
    config.retry_base_ms = UE_RETRY_BASE_MS;
    config.retry_cap_ms = UE_RETRY_CAP_MS;
    config.retry_after_cap_ms = UE_RETRY_AFTER_CAP_MS;
    return config;
}
} // namespace

extern "C" void app_main() {
    // SDK debug logging can include HTTP headers. Keep those components quiet
    // even if an operator temporarily enables verbose application logs.
    esp_log_level_set("HTTP_CLIENT", ESP_LOG_ERROR);
    esp_log_level_set("esp-tls", ESP_LOG_ERROR);
    if (!configuration_ready()) {
        ESP_LOGE(tag, "Not provisioned. Generate private device_config.h, verify wiring, then rebuild and flash.");
        return;
    }
    // Preserve the persisted identity. Never automatically erase NVS on error.
    ESP_ERROR_CHECK(nvs_flash_init());
    sync_queue = xQueueCreate(1, sizeof(TimeSync));
    if (!sync_queue) ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    initialize_wifi_and_time();
    captures = new (queue_storage) Queue(capture_config(), UE_DEVICE_ID, next_boot_identity());
    if (!captures->valid()) ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
    EspTransport transport;
    urbanecho::UploadEngine<Queue> uploader(*captures);
    // Keep DMA draining above the HTTPS worker's priority (4); otherwise a CPU
    // intensive TLS handshake could starve the default low-priority main task.
    // Wi-Fi/TCP-IP SDK tasks remain above this capture executor.
    vTaskPrioritySet(nullptr, 8);
    i2s_chan_handle_t microphone = nullptr;
    uint32_t stereo_slots[dma_frames * 2]{};
    uint64_t frame_number = 0;
    uint64_t next_capture_frame = 0;
    uint32_t recording_frames = 0;
    uint64_t next_wifi_attempt_ms = 0;
    uint32_t wifi_delay_ms = 1000;
    uint64_t last_status_ms = 0;
    uint64_t last_read_ms = monotonic_ms();
    uint64_t dma_faults = 0;
    const uint64_t interval_frames = uint64_t(UE_SAMPLE_RATE) * UE_RECORDING_INTERVAL_MS / 1000;
    ESP_LOGI(tag, "INMP441 GPIO BCLK=%d WS=%d DATA=%d left; PCM24 %d Hz, %d frames; queue=%u bytes",
             UE_GPIO_BCLK, UE_GPIO_WS, UE_GPIO_DATA_IN, UE_SAMPLE_RATE, UE_FRAMES_PER_RECORDING, unsigned(sizeof(Queue)));
    for (;;) {
        const uint64_t now_ms = monotonic_ms();
        if (!wifi_connected.load() && now_ms >= next_wifi_attempt_ms) {
            esp_wifi_connect();
            next_wifi_attempt_ms = now_ms + wifi_delay_ms + esp_random() % 500;
            wifi_delay_ms = std::min(wifi_delay_ms * 2, uint32_t(30000));
        } else if (wifi_connected.load()) wifi_delay_ms = 1000;
        TimeSync sync;
        if (xQueueReceive(sync_queue, &sync, 0) == pdTRUE) {
            const auto update = captures->synchronize(sync.utc_ms, sync.monotonic_ms, true, UE_SNTP_UNCERTAINTY_BUDGET_MS);
            ESP_LOGI(tag, "UTC synchronization state=%d; timestamp accuracy still requires physical verification", int(update));
        }
        uploader.tick(now_ms, transport, UE_DEVICE_TOKEN, esp_random());
        if (!microphone && captures->time_usable(now_ms)) {
            microphone = start_microphone();
            frame_number = 0;
            next_capture_frame = uint64_t(UE_SAMPLE_RATE) * startup_discard_ms / 1000;
            last_read_ms = now_ms;
        }
        if (microphone) {
            size_t read_bytes = 0;
            const auto read_status = i2s_channel_read(microphone, stereo_slots, sizeof stereo_slots, &read_bytes, 20);
            const auto [origin_us, overflow_count] = dma_snapshot();
            const bool failed = overflow_count > 0 || read_bytes % (2 * sizeof(uint32_t)) != 0 ||
                (read_status != ESP_OK && read_status != ESP_ERR_TIMEOUT) ||
                (read_bytes == 0 && now_ms - last_read_ms > 1000);
            if (failed) {
                captures->missed_capture();
                ++dma_faults;
                ESP_LOGW(tag, "Audio DMA gap; incomplete recording dropped and capture clock restarted");
                ESP_ERROR_CHECK(i2s_channel_disable(microphone));
                ESP_ERROR_CHECK(i2s_del_channel(microphone));
                microphone = nullptr;
            } else if (origin_us > 0) {
                if (read_bytes > 0) last_read_ms = now_ms;
                const size_t frames = read_bytes / (2 * sizeof(uint32_t));
                for (size_t frame = 0; frame < frames; ++frame, ++frame_number) {
                    if (frame_number == next_capture_frame) {
                        const uint64_t first_sample_ms = uint64_t(origin_us + int64_t(frame_number * 1000000ULL / UE_SAMPLE_RATE)) / 1000;
                        captures->begin_capture(first_sample_ms);
                        recording_frames = 0;
                        next_capture_frame += interval_frames;
                    }
                    if (captures->capturing()) {
                        // L/R tied to GND selects left, first slot. Right slot
                        // is still clocked/read to supply required 64 BCLK ticks.
                        captures->append_i2s_slot(stereo_slots[frame * 2], UE_I2S_SAMPLE_LSB);
                        if (++recording_frames == UE_FRAMES_PER_RECORDING) captures->finish_capture();
                    }
                }
            }
        } else vTaskDelay(pdMS_TO_TICKS(20));
        if (now_ms - last_status_ms >= 10000) {
            const auto& c = captures->counters();
            ESP_LOGI(tag, "status queued=%u accepted=%" PRIu64 " duplicates=%" PRIu64 " retries=%" PRIu64
                " drop_full=%" PRIu64 " drop_time=%" PRIu64 " drop_capture=%" PRIu64 " drop_retry=%" PRIu64
                " drop_rejected=%" PRIu64 " dma_gaps=%" PRIu64 " paused=%d fault=%d free_heap=%u",
                unsigned(captures->ready()), c.accepted, c.duplicate_acknowledgements, c.retries, c.dropped_overflow,
                c.dropped_unsynchronized, c.dropped_capture, c.dropped_retry_exhausted, c.dropped_rejected,
                dma_faults, uploader.paused(), int(uploader.last_fault()), unsigned(esp_get_free_heap_size()));
            last_status_ms = now_ms;
        }
    }
}
