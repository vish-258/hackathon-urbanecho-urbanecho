#include "urbanecho/device_core.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace urbanecho;
namespace {
constexpr int64_t epoch_ms = 1791540000123LL; // 2026-10-09T10:00:00.123Z
constexpr std::string_view device_id = "226bb57c-d06e-46f0-a7c0-16850a878850";
constexpr std::string_view test_token = "test-only-not-a-real-device-credential";
int checks = 0;
#define CHECK(expr) do { ++checks; if (!(expr)) { std::cerr << "FAIL line " << __LINE__ << ": " #expr "\n"; std::exit(1); } } while (false)

Config small_config() {
    Config c;
    c.frames_per_recording = 3;
    c.recording_interval_ms = 1;
    c.upload_interval_ms = 1;
    return c;
}
BootIdentity identity(uint64_t counter = 1) {
    BootIdentity id;
    id.persisted_boot_counter = counter;
    for (size_t i = 0; i < id.random_nonce.size(); ++i) id.random_nonce[i] = uint8_t(i + 1);
    return id;
}
using Queue = CaptureQueue<3, 2>;
Queue queue(Config config = small_config(), uint64_t boot = 1) {
    Queue result(config, device_id, identity(boot));
    CHECK(result.valid());
    CHECK(result.synchronize(epoch_ms, 100, true, 20) == ClockUpdate::first_sync);
    return result;
}
bool capture(Queue& q, uint64_t mono = 100) {
    if (!q.begin_capture(mono)) return false;
    CHECK(q.append_pcm24(-8388608));
    CHECK(q.append_pcm24(-1));
    CHECK(q.append_pcm24(8388607));
    return q.finish_capture();
}
uint32_t read32(const uint8_t* data) {
    return data[0] | (uint32_t(data[1]) << 8) | (uint32_t(data[2]) << 16) | (uint32_t(data[3]) << 24);
}
std::vector<uint8_t> body(const MultipartRequest& request) {
    std::vector<uint8_t> result;
    for (const auto& part : request.parts()) result.insert(result.end(), part.data, part.data + part.size);
    return result;
}

class FakeTransport : public HttpTransport {
public:
    bool online = true;
    bool trusted = true;
    bool begin_success = true;
    TransferResult response{};
    std::vector<std::vector<uint8_t>> attempts;
    const MultipartRequest* active = nullptr;
    bool network_ready() const override { return online; }
    bool trust_ready() const override { return trusted; }
    bool begin(const MultipartRequest& request, std::string_view credential) override {
        CHECK(credential == test_token);
        CHECK(request.path() == "/audio");
        CHECK(request.method() == "POST");
        attempts.push_back(body(request));
        CHECK(request.content_length() == attempts.back().size());
        active = &request;
        return begin_success;
    }
    TransferResult poll() override {
        if (response.state == TransferState::finished) {
            CHECK(active != nullptr);
            CHECK(body(*active) == attempts.back());
            active = nullptr;
        }
        return response;
    }
    void finish(int status, bool ack = false, uint32_t after = 0) {
        response = {TransferState::finished, status, ack, after};
    }
};

void test_pcm_and_wav() {
    auto q = queue();
    CHECK(capture(q));
    const auto view = q.front();
    CHECK(view.wav_bytes == 54); // odd 9-byte payload includes RIFF padding
    CHECK(read32(view.wav + 4) + 8 == view.wav_bytes);
    CHECK(read32(view.wav + 24) == 16000);
    CHECK(read32(view.wav + 28) == 48000);
    CHECK(read32(view.wav + 40) == 9);
    const std::array<uint8_t, 10> expected{0, 0, 0x80, 0xff, 0xff, 0xff, 0xff, 0xff, 0x7f, 0};
    CHECK(std::memcmp(view.wav + 44, expected.data(), expected.size()) == 0);
    CHECK(decode_i2s_slot(0x80000000, 8) == -8388608);
    CHECK(decode_i2s_slot(0x7fffff00, 8) == 8388607);
    CHECK(decode_i2s_slot(0xffffff00, 8) == -1);
    CHECK(decode_i2s_slot(0x00800000, 0) == -8388608);
    CHECK(!decode_i2s_slot(0, 9));
    CHECK(view.captured_at == "2026-10-09T10:00:00.123Z");
    CHECK(view.sequence == 0);
    CHECK(view.chunk_id.size() <= 128);
    CHECK(view.session_id.size() <= 128);
    CHECK(sizeof(CaptureQueue<>) < 110000);
}

void test_config_and_capture_failures() {
    auto c = small_config();
    c.frames_per_recording = 4;
    CHECK(!Queue(c, device_id, identity()).valid());
    c = small_config(); c.sample_rate = 8000;
    CHECK(!Queue(c, device_id, identity()).valid());
    c = small_config(); c.recording_interval_ms = 0;
    CHECK(!Queue(c, device_id, identity()).valid());
    CHECK(!Queue(small_config(), "not-a-uuid", identity()).valid());
    CHECK(!Queue(small_config(), device_id, BootIdentity{}).valid());
    auto q = queue();
    CHECK(q.begin_capture(100));
    CHECK(!q.append_pcm24(8388608));
    CHECK(!q.finish_capture());
    CHECK(q.counters().dropped_capture == 1);
    CHECK(q.begin_capture(101));
    CHECK(q.append_pcm24(0));
    CHECK(!q.finish_capture());
    CHECK(q.counters().dropped_capture == 2);
    CHECK(capture(q, 102));
    CHECK(q.front().sequence == 2);
}

void test_time_sync_age_jumps_and_reboots() {
    auto c = small_config(); c.max_clock_age_ms = 2000;
    Queue q(c, device_id, identity());
    CHECK(!q.begin_capture(0));
    CHECK(q.counters().dropped_unsynchronized == 1);
    CHECK(q.synchronize(0, 100, true, 20) == ClockUpdate::invalid);
    CHECK(q.synchronize(epoch_ms, 100, false, 20) == ClockUpdate::invalid);
    CHECK(q.synchronize(epoch_ms, 100, true, 101) == ClockUpdate::invalid);
    CHECK(q.synchronize(epoch_ms, 100, true, 20) == ClockUpdate::first_sync);
    CHECK(capture(q, 100));
    const std::string first_session(q.front().session_id);
    const std::string original_timestamp(q.front().captured_at);
    CHECK(!q.begin_capture(2101));
    CHECK(!q.time_usable(2101));
    CHECK(q.synchronize(epoch_ms + 10000, 2200, true, 20) == ClockUpdate::discontinuity);
    CHECK(q.front().captured_at == original_timestamp);
    CHECK(capture(q, 2200));
    q.acknowledge(false);
    CHECK(q.front().session_id != first_session);
    CHECK(q.front().sequence == 3);
    CHECK(q.counters().clock_discontinuities == 1);
    CHECK(q.begin_capture(2300));
    CHECK(q.synchronize(epoch_ms + 20000, 2400, true, 20) == ClockUpdate::discontinuity);
    CHECK(!q.capturing());
    CHECK(q.counters().dropped_capture == 1);
    auto reboot = queue(small_config(), 2);
    CHECK(capture(reboot));
    CHECK(reboot.front().sequence == 0);
    CHECK(reboot.front().session_id != first_session);
}

void test_bounded_queue_and_gaps() {
    auto q = queue();
    CHECK(capture(q, 100));
    CHECK(capture(q, 101));
    const std::string oldest(q.front().chunk_id);
    CHECK(!capture(q, 102));
    CHECK(q.ready() == 2);
    CHECK(q.front().chunk_id == oldest);
    CHECK(q.counters().dropped_overflow == 1);
    q.acknowledge(false);
    CHECK(capture(q, 103));
    q.acknowledge(false);
    CHECK(q.front().sequence == 3);
    q.acknowledge(false);
    q.missed_capture();
    CHECK(capture(q, 104));
    CHECK(q.front().sequence == 5);
    CHECK(q.counters().recording_opportunities == 6);
}

void test_multipart_and_lost_ack_retry() {
    auto q = queue(); CHECK(capture(q));
    MultipartRequest multipart;
    CHECK(multipart.build(q.front()));
    CHECK(multipart.boundary().size() <= 70);
    CHECK(multipart.parts()[1].data == q.front().wav); // no PCM copy
    const auto bytes = body(multipart);
    const std::string text(bytes.begin(), bytes.end());
    CHECK(text.find("name=\"metadata\"") != std::string::npos);
    CHECK(text.find("\"captured_at\":\"2026-10-09T10:00:00.123Z\"") != std::string::npos);
    CHECK(text.find("name=\"file\"; filename=\"capture.wav\"") != std::string::npos);
    CHECK(text.find(test_token) == std::string::npos);
    UploadEngine engine(q); FakeTransport transport;
    engine.tick(100, transport, test_token, 0);
    CHECK(engine.in_flight());
    CHECK(q.begin_capture(101));
    CHECK(q.append_pcm24(1)); // capturing into other slot cannot corrupt upload
    transport.finish(0); engine.tick(102, transport, test_token, 0);
    CHECK(q.ready() == 1);
    CHECK(engine.next_attempt_ms() == 852);
    engine.tick(851, transport, test_token, 0);
    CHECK(transport.attempts.size() == 1);
    CHECK(q.append_pcm24(2)); CHECK(q.append_pcm24(3)); CHECK(q.finish_capture());
    engine.tick(852, transport, test_token, 0);
    CHECK(transport.attempts.size() == 2);
    CHECK(transport.attempts[0] == transport.attempts[1]);
    transport.finish(200, true); engine.tick(853, transport, test_token, 0);
    CHECK(q.ready() == 1);
    CHECK(q.front().sequence == 1);
    CHECK(q.counters().accepted == 1);
    CHECK(q.counters().duplicate_acknowledgements == 1);
}

void test_network_and_trust_gates() {
    auto q = queue(); CHECK(capture(q));
    UploadEngine engine(q); FakeTransport transport;
    transport.online = false;
    engine.tick(100, transport, test_token, 0);
    CHECK(transport.attempts.empty());
    CHECK(capture(q, 101)); CHECK(!capture(q, 102));
    transport.online = true; transport.trusted = false;
    engine.tick(103, transport, test_token, 0);
    CHECK(transport.attempts.empty());
    transport.trusted = true;
    engine.tick(104, transport, test_token, 0);
    transport.finish(202, true); engine.tick(105, transport, test_token, 0);
    CHECK(q.ready() == 1);
    CHECK(q.counters().dropped_overflow == 1);
}

void test_http_policy() {
    for (int status : {0, 408, 429, 500, 502, 503}) {
        auto q = queue(); CHECK(capture(q));
        UploadEngine engine(q); FakeTransport transport;
        engine.tick(100, transport, test_token, 0);
        transport.finish(status, false, status == 429 ? 4000 : 0);
        engine.tick(101, transport, test_token, 0);
        CHECK(!engine.paused()); CHECK(q.ready() == 1);
        CHECK(q.counters().retries == 1);
        CHECK(engine.next_attempt_ms() == uint64_t(status == 429 ? 4101 : 851));
    }
    for (int status : {401, 403}) {
        auto q = queue(); CHECK(capture(q));
        const std::string id(q.front().chunk_id);
        UploadEngine engine(q); FakeTransport transport;
        engine.tick(100, transport, test_token, 0);
        transport.finish(status); engine.tick(101, transport, test_token, 0);
        CHECK(engine.paused()); CHECK(q.ready() == 1); CHECK(q.front().chunk_id == id);
        engine.tick(99999, transport, test_token, 0);
        CHECK(transport.attempts.size() == 1);
        engine.resume_after_operator_action(100000);
        engine.tick(100000, transport, test_token, 0);
        transport.finish(202, true); engine.tick(100001, transport, test_token, 0);
        CHECK(q.ready() == 0); CHECK(transport.attempts[0] == transport.attempts[1]);
    }
    for (int status : {400, 404, 413, 415, 422, 302}) {
        auto q = queue(); CHECK(capture(q));
        UploadEngine engine(q); FakeTransport transport;
        engine.tick(100, transport, test_token, 0);
        transport.finish(status); engine.tick(101, transport, test_token, 0);
        CHECK(engine.paused()); CHECK(q.ready() == 0);
        CHECK(q.counters().dropped_rejected == 1);
    }
    auto q = queue(); CHECK(capture(q));
    UploadEngine engine(q); FakeTransport transport;
    engine.tick(100, transport, test_token, 0);
    transport.finish(409); engine.tick(101, transport, test_token, 0);
    CHECK(!engine.paused()); CHECK(q.ready() == 0);
    CHECK(engine.last_fault() == UploadFault::conflict);
}

void test_retry_exhaustion_and_response_validation() {
    auto c = small_config(); c.max_attempts = 2;
    auto q = queue(c); CHECK(capture(q));
    UploadEngine engine(q); FakeTransport transport;
    engine.tick(100, transport, test_token, 0);
    transport.finish(202, false); engine.tick(101, transport, test_token, 0);
    CHECK(q.ready() == 1); // 2xx HTML/invalid JSON must not discard capture
    const auto retry_at = engine.next_attempt_ms();
    engine.tick(retry_at, transport, test_token, 0);
    transport.finish(503); engine.tick(retry_at + 1, transport, test_token, 0);
    CHECK(q.ready() == 0);
    CHECK(q.counters().dropped_retry_exhausted == 1);
    CHECK(engine.last_fault() == UploadFault::retry_exhausted);
}

void test_upload_cadence_does_not_accumulate_network_latency() {
    auto c = small_config(); c.upload_interval_ms = 1000;
    auto q = queue(c); CHECK(capture(q)); CHECK(capture(q, 1100));
    UploadEngine engine(q); FakeTransport transport;
    engine.tick(100, transport, test_token, 0);
    transport.finish(202, true); engine.tick(300, transport, test_token, 0);
    CHECK(engine.next_attempt_ms() == 1100);
    engine.tick(1100, transport, test_token, 0);
    CHECK(transport.attempts.size() == 2);
}

void write_fixture(const std::filesystem::path& directory) {
    std::filesystem::create_directories(directory);
    auto q = queue(); CHECK(capture(q));
    MultipartRequest request; CHECK(request.build(q.front()));
    const auto multipart = body(request);
    std::ofstream wav(directory / "firmware-fixture.wav", std::ios::binary);
    wav.write(reinterpret_cast<const char*>(q.front().wav), std::streamsize(q.front().wav_bytes));
    std::ofstream wire(directory / "firmware-multipart.bin", std::ios::binary);
    wire.write(reinterpret_cast<const char*>(multipart.data()), std::streamsize(multipart.size()));
    std::ofstream manifest(directory / "firmware-request.json");
    manifest << "{\"boundary\":\"" << request.boundary() << "\",\"content_length\":" << request.content_length() << "}\n";
    CHECK(bool(wav) && bool(wire) && bool(manifest));
}
} // namespace

int main(int argc, char** argv) {
    test_pcm_and_wav();
    test_config_and_capture_failures();
    test_time_sync_age_jumps_and_reboots();
    test_bounded_queue_and_gaps();
    test_multipart_and_lost_ack_retry();
    test_network_and_trust_gates();
    test_http_policy();
    test_retry_exhaustion_and_response_validation();
    test_upload_cadence_does_not_accumulate_network_latency();
    if (argc == 3 && std::string_view(argv[1]) == "--write-fixture") write_fixture(argv[2]);
    else if (argc != 1) { std::cerr << "Usage: test-device-core [--write-fixture DIRECTORY]\n"; return 2; }
    std::cout << "PASS: " << checks << " assertions across 9 device-core test groups\n"
              << "Default capture queue: " << sizeof(CaptureQueue<>) << " bytes, including "
              << CaptureQueue<>::audio_capacity_bytes << " PCM/WAV buffer bytes\n";
}
