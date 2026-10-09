#pragma once

// Hardware-independent transport/capture state. No GPIO, Wi-Fi, TLS, or I2S
// driver is selected here. All entry points run on one serialized executor.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <limits>
#include <optional>
#include <string_view>

namespace urbanecho {

struct Config {
    uint32_t sample_rate = 16000;
    uint32_t frames_per_recording = 16000;
    uint32_t recording_interval_ms = 1000;
    uint32_t upload_interval_ms = 1000;
    uint64_t max_clock_age_ms = 60 * 60 * 1000;
    uint32_t clock_jump_tolerance_ms = 250;
    uint32_t max_sync_uncertainty_ms = 100;
    uint32_t retry_base_ms = 1000;
    uint32_t retry_cap_ms = 30000;
    uint32_t retry_after_cap_ms = 300000;
    uint32_t max_attempts = 8;

    bool valid(size_t frame_capacity) const {
        const bool rate = sample_rate == 16000 || sample_rate == 32000 ||
                          sample_rate == 44100 || sample_rate == 48000;
        return rate && frames_per_recording > 0 && frames_per_recording <= frame_capacity &&
               frames_per_recording <= uint64_t(sample_rate) * 60 &&
               uint64_t(recording_interval_ms) * sample_rate >= uint64_t(frames_per_recording) * 1000 &&
               upload_interval_ms > 0 && max_clock_age_ms > 0 && max_clock_age_ms <= 24 * 60 * 60 * 1000ULL &&
               retry_base_ms > 0 && retry_cap_ms >= retry_base_ms &&
               retry_after_cap_ms >= retry_cap_ms && max_attempts > 0 && max_attempts <= 100;
    }
};

// Adapter obtains an atomic, persisted increment BEFORE capture, and a fresh
// cryptographically random 128-bit nonce. No default/random-host fallback.
struct BootIdentity {
    uint64_t persisted_boot_counter = 0;
    std::array<uint8_t, 16> random_nonce{};
    bool valid() const {
        bool nonzero = false;
        for (auto byte : random_nonce) nonzero |= byte != 0;
        return persisted_boot_counter != 0 && nonzero;
    }
};

enum class ClockUpdate { invalid, first_sync, maintained, discontinuity };

class CaptureClock {
public:
    explicit CaptureClock(const Config& config) : config_(config) {}

    // UTC is Unix milliseconds. monotonic_ms must be an extended 64-bit clock,
    // not wrapping 32-bit millis(). Adapter must validate the synchronization.
    ClockUpdate synchronize(int64_t utc_ms, uint64_t monotonic_ms, bool source_usable, uint32_t uncertainty_ms) {
        constexpr int64_t earliest = 1577836800000LL; // 2020-01-01
        constexpr int64_t latest = 4102444800000LL;   // 2100-01-01
        if (!source_usable || uncertainty_ms > config_.max_sync_uncertainty_ms ||
            utc_ms < earliest || utc_ms >= latest) return ClockUpdate::invalid;
        ClockUpdate result = ClockUpdate::first_sync;
        if (valid_) {
            const bool monotonic_reset = monotonic_ms < anchor_monotonic_ms_;
            const uint64_t elapsed = monotonic_reset ? 0 : monotonic_ms - anchor_monotonic_ms_;
            const bool huge_elapsed = elapsed > uint64_t(latest - earliest);
            const int64_t predicted = huge_elapsed ? 0 : anchor_utc_ms_ + int64_t(elapsed);
            const bool jump = monotonic_reset || huge_elapsed ||
                std::llabs(utc_ms - predicted) > int64_t(config_.clock_jump_tolerance_ms);
            if (jump) {
                if (generation_ == std::numeric_limits<uint32_t>::max()) {
                    valid_ = false;
                    return ClockUpdate::invalid;
                }
                ++generation_;
                result = ClockUpdate::discontinuity;
            } else {
                result = ClockUpdate::maintained;
            }
        }
        // Already captured/queued timestamps never change after a clock update.
        anchor_utc_ms_ = utc_ms;
        anchor_monotonic_ms_ = monotonic_ms;
        valid_ = true;
        return result;
    }

    std::optional<int64_t> at(uint64_t monotonic_ms) const {
        if (!valid_ || monotonic_ms < anchor_monotonic_ms_) return std::nullopt;
        const uint64_t age = monotonic_ms - anchor_monotonic_ms_;
        if (age > config_.max_clock_age_ms) return std::nullopt;
        return anchor_utc_ms_ + int64_t(age);
    }
    uint32_t generation() const { return generation_; }

private:
    Config config_;
    bool valid_ = false;
    int64_t anchor_utc_ms_ = 0;
    uint64_t anchor_monotonic_ms_ = 0;
    uint32_t generation_ = 0;
};

inline bool uuid_valid(std::string_view value) {
    if (value.size() != 36) return false;
    for (size_t i = 0; i < value.size(); ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (value[i] != '-') return false;
        } else if (!((value[i] >= '0' && value[i] <= '9') ||
                     (value[i] >= 'a' && value[i] <= 'f') ||
                     (value[i] >= 'A' && value[i] <= 'F'))) return false;
    }
    return true;
}

inline bool format_utc(int64_t utc_ms, char (&out)[25]) {
    if (utc_ms < 1577836800000LL || utc_ms >= 4102444800000LL) return false;
    const auto seconds = static_cast<std::time_t>(utc_ms / 1000);
    std::tm converted{};
#if defined(_WIN32)
    if (gmtime_s(&converted, &seconds) != 0) return false;
#elif defined(__unix__) || defined(__APPLE__) || defined(ESP_PLATFORM)
    if (!gmtime_r(&seconds, &converted)) return false;
#else
    // Exotic standard-only targets require serialized use of the C time APIs.
    const auto* shared = std::gmtime(&seconds);
    if (!shared) return false;
    converted = *shared;
#endif
    const auto* time = &converted;
    return std::snprintf(out, sizeof out, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                         time->tm_year + 1900, time->tm_mon + 1, time->tm_mday,
                         time->tm_hour, time->tm_min, time->tm_sec, int(utc_ms % 1000)) == 24;
}

// sample_lsb is the bit index of the signed 24-bit payload within one DMA slot.
// It must be measured/verified for the chosen microphone + ESP driver. Neither
// left/right channel extraction nor unknown alignment is guessed here.
inline std::optional<int32_t> decode_i2s_slot(uint32_t slot, unsigned sample_lsb) {
    if (sample_lsb > 8) return std::nullopt;
    const uint32_t raw = (slot >> sample_lsb) & 0x00ffffffU;
    return raw & 0x00800000U ? int32_t(int64_t(raw) - 0x01000000LL) : int32_t(raw);
}

inline void put16(uint8_t* target, uint16_t value) {
    target[0] = uint8_t(value); target[1] = uint8_t(value >> 8);
}
inline void put32(uint8_t* target, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) target[i] = uint8_t(value >> (8 * i));
}
inline size_t make_wav_header(uint8_t* out, uint32_t frames, uint32_t rate) {
    const uint32_t payload = frames * 3;
    const uint32_t padding = payload & 1U;
    std::memcpy(out, "RIFF", 4); put32(out + 4, 36 + payload + padding);
    std::memcpy(out + 8, "WAVEfmt ", 8); put32(out + 16, 16);
    put16(out + 20, 1); put16(out + 22, 1); put32(out + 24, rate);
    put32(out + 28, rate * 3); put16(out + 32, 3); put16(out + 34, 24);
    std::memcpy(out + 36, "data", 4); put32(out + 40, payload);
    return 44 + payload + padding;
}

struct Counters {
    uint64_t recording_opportunities = 0;
    uint64_t enqueued = 0;
    uint64_t accepted = 0;
    uint64_t duplicate_acknowledgements = 0;
    uint64_t upload_attempts = 0;
    uint64_t retries = 0;
    uint64_t dropped_overflow = 0;
    uint64_t dropped_unsynchronized = 0;
    uint64_t dropped_capture = 0;
    uint64_t dropped_retry_exhausted = 0;
    uint64_t dropped_rejected = 0;
    uint64_t clock_discontinuities = 0;
};

struct RecordingView {
    std::string_view device_id;
    std::string_view chunk_id;
    std::string_view session_id;
    std::string_view captured_at;
    uint64_t sequence = 0;
    const uint8_t* wav = nullptr;
    size_t wav_bytes = 0;
};

template<size_t MaxFrames = 16000, size_t Slots = 2>
class CaptureQueue {
    static_assert(MaxFrames > 0 && MaxFrames <= 48000 * 60, "Invalid PCM capacity");
    static_assert(Slots >= 2 && Slots <= 8, "Use a bounded 2-8 slot queue");
    struct Recording {
        std::array<uint8_t, 44 + MaxFrames * 3 + 1> wav{};
        char chunk_id[128]{};
        char session_id[96]{};
        char captured_at[25]{};
        uint64_t sequence = 0;
        size_t frames = 0;
        size_t wav_bytes = 0;
    };

public:
    CaptureQueue(Config config, std::string_view device_id, BootIdentity identity)
        : config_(config), clock_(config), identity_(identity) {
        valid_ = config.valid(MaxFrames) && uuid_valid(device_id) && identity.valid();
        if (valid_) std::memcpy(device_id_, device_id.data(), 36);
    }
    bool valid() const { return valid_; }
    const Config& config() const { return config_; }
    const Counters& counters() const { return counters_; }
    size_t ready() const { return ready_; }
    bool capturing() const { return capturing_; }
    static constexpr size_t audio_capacity_bytes = Slots * (44 + MaxFrames * 3 + 1);

    ClockUpdate synchronize(int64_t utc_ms, uint64_t monotonic_ms, bool usable, uint32_t uncertainty_ms) {
        const auto result = clock_.synchronize(utc_ms, monotonic_ms, usable, uncertainty_ms);
        if (result == ClockUpdate::discontinuity) {
            ++counters_.clock_discontinuities;
            // A recording straddling a clock correction cannot assert one
            // trustworthy timestamp. Existing complete recordings are retained.
            abort_capture();
        }
        return result;
    }
    bool time_usable(uint64_t now_ms) const { return clock_.at(now_ms).has_value(); }

    // Called once per configured capture opportunity, even when the queue is
    // full. The timestamp MUST correspond to the first captured sample, not the
    // end of DMA read or the eventual upload. Sequence advances on dropped data.
    bool begin_capture(uint64_t first_sample_monotonic_ms) {
        if (!valid_ || capturing_ || sequence_ > uint64_t(INT64_MAX)) return false;
        const uint64_t sequence = sequence_++;
        ++counters_.recording_opportunities;
        const auto utc = clock_.at(first_sample_monotonic_ms);
        if (!utc) { ++counters_.dropped_unsynchronized; return false; }
        if (ready_ == Slots) { ++counters_.dropped_overflow; return false; }
        capture_slot_ = (head_ + ready_) % Slots;
        auto& recording = recordings_[capture_slot_];
        recording.frames = 0;
        recording.sequence = sequence;
        if (!format_utc(*utc, recording.captured_at)) {
            ++counters_.dropped_unsynchronized;
            return false;
        }
        char nonce[33]{};
        for (size_t i = 0; i < identity_.random_nonce.size(); ++i)
            std::snprintf(nonce + i * 2, 3, "%02x", identity_.random_nonce[i]);
        char session_id[96]{};
        std::snprintf(session_id, sizeof session_id, "%s-b%016llx-t%08x",
                      nonce, static_cast<unsigned long long>(identity_.persisted_boot_counter), unsigned(clock_.generation()));
        std::memcpy(recording.session_id, session_id, sizeof session_id);
        std::snprintf(recording.chunk_id, sizeof recording.chunk_id, "%s-q%016llx",
                      session_id, static_cast<unsigned long long>(sequence));
        capturing_ = true;
        return true;
    }

    bool append_pcm24(int32_t sample) {
        if (!capturing_) return false;
        auto& recording = recordings_[capture_slot_];
        if (sample < -8388608 || sample > 8388607 || recording.frames >= config_.frames_per_recording) {
            abort_capture();
            return false;
        }
        const auto packed = uint32_t(sample) & 0x00ffffffU;
        auto* target = recording.wav.data() + 44 + recording.frames++ * 3;
        target[0] = uint8_t(packed); target[1] = uint8_t(packed >> 8); target[2] = uint8_t(packed >> 16);
        return true;
    }
    bool append_i2s_slot(uint32_t slot, unsigned sample_lsb) {
        const auto value = decode_i2s_slot(slot, sample_lsb);
        if (!value) { abort_capture(); return false; }
        return append_pcm24(*value);
    }
    bool finish_capture() {
        if (!capturing_) return false;
        auto& recording = recordings_[capture_slot_];
        if (recording.frames != config_.frames_per_recording) { abort_capture(); return false; }
        recording.wav_bytes = make_wav_header(recording.wav.data(), uint32_t(recording.frames), config_.sample_rate);
        if ((recording.frames * 3) & 1) recording.wav[recording.wav_bytes - 1] = 0;
        capturing_ = false;
        ++ready_;
        ++counters_.enqueued;
        return true;
    }
    void abort_capture() {
        if (capturing_) { capturing_ = false; ++counters_.dropped_capture; }
    }
    // Adapter uses this when an entire scheduled capture was lost before DMA
    // could supply a first sample, e.g. an overrun. Do not fabricate silent WAVs.
    void missed_capture() {
        if (capturing_) { abort_capture(); return; }
        if (sequence_ <= uint64_t(INT64_MAX)) ++sequence_;
        ++counters_.recording_opportunities;
        ++counters_.dropped_capture;
    }
    RecordingView front() const {
        if (ready_ == 0) return {};
        const auto& recording = recordings_[head_];
        return {device_id_, recording.chunk_id, recording.session_id, recording.captured_at,
                recording.sequence, recording.wav.data(), recording.wav_bytes};
    }

    // Only UploadEngine may acknowledge/drop the FIFO head. Do not call these
    // from another task while a transport references its immutable body spans.
    void acknowledge(bool duplicate) {
        if (!ready_) return;
        ++counters_.accepted;
        if (duplicate) ++counters_.duplicate_acknowledgements;
        pop();
    }
    void reject(bool exhausted) {
        if (!ready_) return;
        if (exhausted) ++counters_.dropped_retry_exhausted;
        else ++counters_.dropped_rejected;
        pop();
    }
    void note_attempt() { ++counters_.upload_attempts; }
    void note_retry() { ++counters_.retries; }

private:
    void pop() { head_ = (head_ + 1) % Slots; --ready_; }
    Config config_;
    CaptureClock clock_;
    BootIdentity identity_;
    bool valid_ = false;
    char device_id_[37]{};
    std::array<Recording, Slots> recordings_{};
    size_t head_ = 0;
    size_t ready_ = 0;
    size_t capture_slot_ = 0;
    bool capturing_ = false;
    uint64_t sequence_ = 0;
    Counters counters_;
};

struct ByteSpan { const uint8_t* data = nullptr; size_t size = 0; };

// Owns only short headers/metadata. The WAV is referenced in-place; no second
// recording-sized allocation or concatenated multipart body is created.
class MultipartRequest {
public:
    bool build(RecordingView recording) {
        if (!recording.wav || !recording.wav_bytes || !uuid_valid(recording.device_id) ||
            recording.chunk_id.empty() || recording.chunk_id.size() > 127 ||
            recording.session_id.size() < 40 || recording.session_id.size() > 95 ||
            recording.captured_at.size() != 24) return false;
        // <=70 characters, unique to nonce/sequence/clock generation. Boundary
        // contains only the allowed generated ID characters, not credentials.
        const int boundary_count = std::snprintf(boundary_, sizeof boundary_, "ue-%.32s-%016llx-%.8s",
            recording.session_id.data(), static_cast<unsigned long long>(recording.sequence),
            recording.session_id.data() + recording.session_id.size() - 8);
        if (boundary_count < 0 || size_t(boundary_count) >= sizeof boundary_) return false;
        const int prefix_count = std::snprintf(prefix_, sizeof prefix_,
            "--%s\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n"
            "{\"device_id\":\"%.*s\",\"chunk_id\":\"%.*s\",\"captured_at\":\"%.*s\",\"session_id\":\"%.*s\",\"sequence\":%llu}"
            "\r\n--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"capture.wav\"\r\nContent-Type: audio/wav\r\n\r\n",
            boundary_, int(recording.device_id.size()), recording.device_id.data(),
            int(recording.chunk_id.size()), recording.chunk_id.data(), int(recording.captured_at.size()), recording.captured_at.data(),
            int(recording.session_id.size()), recording.session_id.data(), static_cast<unsigned long long>(recording.sequence), boundary_);
        const int suffix_count = std::snprintf(suffix_, sizeof suffix_, "\r\n--%s--\r\n", boundary_);
        if (prefix_count < 0 || size_t(prefix_count) >= sizeof prefix_ ||
            suffix_count < 0 || size_t(suffix_count) >= sizeof suffix_) return false;
        parts_ = {{{reinterpret_cast<const uint8_t*>(prefix_), size_t(prefix_count)},
                   {recording.wav, recording.wav_bytes},
                   {reinterpret_cast<const uint8_t*>(suffix_), size_t(suffix_count)}}};
        return true;
    }
    std::string_view method() const { return "POST"; }
    std::string_view path() const { return "/audio"; }
    std::string_view boundary() const { return boundary_; }
    const std::array<ByteSpan, 3>& parts() const { return parts_; }
    size_t content_length() const { return parts_[0].size + parts_[1].size + parts_[2].size; }

private:
    char boundary_[71]{};
    char prefix_[1400]{};
    char suffix_[144]{};
    std::array<ByteSpan, 3> parts_{};
};

enum class TransferState { pending, finished };
struct TransferResult {
    TransferState state = TransferState::pending;
    int http_status = 0; // 0 = transport error, timeout, or lost acknowledgement.
    // Adapter parses the bounded JSON response and validates UUID id, known
    // processing status, and duplicate=false for 202 / duplicate=true for 200.
    // HTML/login responses and redirects are never acknowledgements.
    bool valid_audio_ack = false;
    uint32_t retry_after_ms = 0;
};

class HttpTransport {
public:
    virtual ~HttpTransport() = default;
    virtual bool network_ready() const = 0;
    // HTTPS requires trusted UTC plus CA + hostname validation, never insecure
    // TLS. Explicitly approved private LAN HTTP needs a different adapter policy.
    virtual bool trust_ready() const = 0;
    // begin/poll are bounded and nonblocking; retain references until finished.
    // Set Authorization: Bearer <device_token> and Content-Type: multipart/form-
    // data; boundary=<request.boundary()>, exact Content-Length. Never redirect.
    virtual bool begin(const MultipartRequest& request, std::string_view device_token) = 0;
    virtual TransferResult poll() = 0;
};

enum class UploadFault { none, network_or_server, authentication, configuration, conflict, retry_exhausted };

template<class Queue>
class UploadEngine {
public:
    explicit UploadEngine(Queue& queue) : queue_(queue) {}
    bool paused() const { return paused_; }
    bool in_flight() const { return in_flight_; }
    UploadFault last_fault() const { return last_fault_; }
    uint64_t next_attempt_ms() const { return next_attempt_ms_; }
    uint32_t attempts_for_head() const { return attempts_; }

    // Only call after credentials/server configuration has been corrected. The
    // immutable front recording is preserved for authentication failures.
    void resume_after_operator_action(uint64_t now_ms) {
        if (in_flight_) return;
        paused_ = false; attempts_ = 0; next_attempt_ms_ = now_ms;
    }

    // random_word comes from the board RNG and only controls retry jitter.
    // Call frequently beside capture draining. A real transport must have its
    // own finite timeout; network disconnect cannot leave poll pending forever.
    void tick(uint64_t now_ms, HttpTransport& transport, std::string_view device_token, uint32_t random_word) {
        if (!queue_.valid() || paused_) return;
        if (in_flight_) {
            const auto result = transport.poll();
            if (result.state == TransferState::pending) return;
            in_flight_ = false;
            complete(now_ms, result, random_word);
            return;
        }
        if (!queue_.ready() || now_ms < next_attempt_ms_ ||
            !transport.network_ready() || !transport.trust_ready()) return;
        if (device_token.empty() || device_token.find_first_of("\r\n") != std::string_view::npos) {
            paused_ = true; last_fault_ = UploadFault::authentication; return;
        }
        if (!request_.build(queue_.front())) {
            queue_.reject(false); paused_ = true; last_fault_ = UploadFault::configuration; return;
        }
        ++attempts_; queue_.note_attempt(); last_started_ms_ = now_ms;
        if (transport.begin(request_, device_token)) in_flight_ = true;
        else complete(now_ms, {TransferState::finished, 0, false, 0}, random_word);
    }

private:
    void complete(uint64_t now_ms, TransferResult result, uint32_t random_word) {
        const int status = result.http_status;
        if ((status == 200 || status == 202) && result.valid_audio_ack) {
            queue_.acknowledge(status == 200); attempts_ = 0;
            last_fault_ = UploadFault::none;
            next_attempt_ms_ = std::max(now_ms, last_started_ms_ + queue_.config().upload_interval_ms);
        } else if (status == 401 || status == 403) {
            paused_ = true; last_fault_ = UploadFault::authentication;
        } else if (status == 409) {
            queue_.reject(false); attempts_ = 0;
            last_fault_ = UploadFault::conflict;
            next_attempt_ms_ = now_ms + queue_.config().upload_interval_ms;
        } else if (status == 0 || status == 408 || status == 429 || status >= 500 ||
                   ((status == 200 || status == 202) && !result.valid_audio_ack)) {
            last_fault_ = UploadFault::network_or_server;
            if (attempts_ >= queue_.config().max_attempts) {
                queue_.reject(true); attempts_ = 0;
                last_fault_ = UploadFault::retry_exhausted;
                next_attempt_ms_ = now_ms + queue_.config().upload_interval_ms;
                return;
            }
            uint64_t delay = queue_.config().retry_base_ms;
            for (uint32_t i = 1; i < attempts_ && delay < queue_.config().retry_cap_ms; ++i)
                delay = std::min(delay * 2, uint64_t(queue_.config().retry_cap_ms));
            // Jitter 75-100% of capped backoff. Respect bounded Retry-After.
            const uint64_t minimum = delay * 3 / 4;
            delay = minimum + random_word % (delay - minimum + 1);
            delay = std::max(delay, uint64_t(std::min(result.retry_after_ms, queue_.config().retry_after_cap_ms)));
            next_attempt_ms_ = now_ms + delay;
            queue_.note_retry();
        } else {
            // 413/422 and other unexpected 4xx/redirect responses indicate a
            // contract/configuration error; stop instead of retrying forever.
            queue_.reject(false); attempts_ = 0;
            paused_ = true; last_fault_ = UploadFault::configuration;
        }
    }

    Queue& queue_;
    MultipartRequest request_;
    uint64_t next_attempt_ms_ = 0;
    uint64_t last_started_ms_ = 0;
    uint32_t attempts_ = 0;
    bool paused_ = false;
    bool in_flight_ = false;
    UploadFault last_fault_ = UploadFault::none;
};

} // namespace urbanecho
