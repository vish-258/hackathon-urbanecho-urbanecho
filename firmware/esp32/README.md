# ESP-WROOM-32 + INMP441 firmware

This is the C++ ESP-IDF firmware project for the confirmed **classic ESP32 / ESP-WROOM-32** module and INMP441 microphone. It can be built and flashed on the other computer connected to the board; the backend continues running on the original computer. It is an ESP-IDF project, not an Arduino `.ino` sketch.

The checked toolchain is **ESP-IDF v5.4.3**, target **esp32**. Native core tests and an ESP target compilation establish software/build behavior. They do not prove that a microphone is connected, that its wiring works, or that sound levels are acoustically accurate. See the root hardware integration report for the current physical-testing status.

## Confirm the wiring before powering the board

Use GPIO labels, not physical header positions: the development-board carrier revision has not been identified.

| INMP441 | Confirmed ESP32 connection |
|---|---|
| VDD | 3.3 V |
| GND | GND |
| SCK / BCLK | GPIO26 |
| WS / LRCLK | GPIO25 |
| SD / DATA | GPIO33 |
| L/R | GND, selecting left channel |

Do not connect the microphone to 5 V. No MCLK wire is required by this selected interface. Confirm the actual module/breakout markings and common ground; see [hardware integration](../../docs/HARDWARE-INTEGRATION.md) for component-source references and the full connection checklist.

The adapter uses Philips standard I2S, two 32-bit slots per frame, and selects the left slot. It extracts the signed high 24 bits and writes packed little-endian three-byte samples. It supplies 64 BCLK clocks per stereo frame even though the uploaded WAV is mono. It discards the first 400 milliseconds after starting/restarting I2S. The [official ESP-IDF I2S documentation](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32/api-reference/peripherals/i2s.html) describes the slot/Philips configuration. On-board channel alignment, clean captures, clock rate, and microphone response still need to be checked.

## Prepare private configuration

1. Register one physical device using the project registration helper and the `esp32-wroom-32-inmp441` profile. Use a physical location with a **one-second dBFS** rule until real acoustic calibration is available. A simulated calibration offset must never be copied to this microphone.
2. Obtain the private `.local/hardware-device.json` registration file and the server's **public** CA certificate `.local/hardware-tls/ca.crt`. Copy those files securely to the flashing computer. Do not copy the CA/server private keys. A shareable source ZIP intentionally contains neither device credentials nor provisioned firmware binaries.
3. Edit the private JSON as UTF-8. Set `wifi.ssid` and `wifi.password` for a WPA2-capable **2.4 GHz** Wi-Fi network. Set `endpoint` to the backend computer's LAN HTTPS address, for example `https://192.168.1.20:8443`; `localhost` would mean the ESP itself. The ESP and backend computer must share a reachable LAN. The flashing computer does not need to remain connected after loading the firmware. The optional device-only listener and firewall setup are explained in the root hardware guide.
4. Verify the wiring and set `board.wiring_verified` to `true`. The supplied profile must retain GPIO26/25/33, left channel, and sample offset 8. A different board/wiring needs a reviewed adapter/configuration change.
5. Run this from the project root in a Python 3 environment:

```sh
python firmware/esp32/configure.py --config .local/hardware-device.json --ca .local/hardware-tls/ca.crt
```

This writes `firmware/esp32/main/device_config.h` with owner-only permissions where supported. It does not print credentials. That header, private JSON, and any firmware binary built from it must remain private. Device credentials and Wi-Fi details are embedded in flash by this development workflow; flash encryption/secure provisioning are not implemented.

`../config.example.json` documents the fields. The generator accepts the registered file, so it does not require copying a token into a command or the web application. Missing credentials, HTTP/loopback URLs, a mismatched or unverified board profile, excessive buffers, and private keys supplied as CA certificates are rejected.

## Build and flash on the board's computer

Install/open the official [ESP-IDF v5.4.3 environment](https://docs.espressif.com/projects/esp-idf/en/v5.4.3/esp32/get-started/index.html). Use its terminal on Windows, macOS, or Linux so Python, CMake, Ninja, and the Xtensa compiler are on the correct paths. Then, from this repository root:

```sh
idf.py -C firmware/esp32 set-target esp32
idf.py -C firmware/esp32 build
```

Before flashing, identify the actual USB serial port and read the chip/flash identity using the installed `esptool.py` / `python -m esptool` version's `flash_id` command. The supplied build defaults to 4 MB flash because the exact carrier/flash variant is still unverified. If the reported size differs, set the correct flash size in `idf.py -C firmware/esp32 menuconfig` and rebuild; do not assume the module label guarantees 4 MB.

Replace the example port with the connected board's real port:

```sh
idf.py -C firmware/esp32 -p YOUR_SERIAL_PORT flash monitor
```

Typical port forms are `/dev/cu.usbserial-...` on macOS, `/dev/ttyUSB0` on Linux, and `COM5` on Windows. These are examples, not detected ports. Some development boards need the BOOT button held while the flashing tool connects. Consult the identified carrier's instructions if automatic reset does not work. Exit the monitor with Ctrl+].

For a compiler-only container check, the pinned official image is `espressif/idf:v5.4.3`. Use `configure.py --compile-check --ca PATH_TO_PUBLIC_CA` to generate a fake credential profile and reserved documentation endpoint for a complete link/memory check. **Do not flash the compile-check profile.** It cannot connect to the actual backend. The public blank example header also compiles but refuses to start; it is not sufficient for checking the complete linked runtime size.

## Runtime behavior

- Wi-Fi reconnects with bounded backoff. HTTPS runs in a separate FreeRTOS task while the main executor continues draining audio DMA. Only frozen upload buffers cross between tasks.
- The firmware waits for SNTP before capturing or starting TLS. The [Espressif SNTP example](https://github.com/espressif/esp-idf/blob/v5.4.3/examples/protocols/sntp/main/sntp_example_main.c) documents the SDK synchronization facilities used here. The default server is `pool.ntp.org`; network DNS and NTP access must work. Refresh is every 15 minutes, and a clock anchor becomes unusable after one hour.
- Capture timestamps are derived from the first DMA completion's monotonic time minus its sample duration, then advanced by sample count. Upload time is never substituted. A clock jump over 250 ms changes the session and drops an unfinished recording. The 100 ms uncertainty setting is a configured time budget, **not a measurement of actual SNTP or microphone-clock error**; check device timestamps against the server during the physical test.
- Each boot atomically increments an NVS boot counter and uses a fresh random nonce. Each scheduled recording consumes a sequence, even if dropped. NVS errors halt initialization rather than silently erasing identity.
- This ESP-WROOM RAM profile supports **one-second recordings at 16 kHz**. A WAV is 48,044 bytes plus multipart overhead. Recording cadence and upload cadence are configurable; defaults are every second. The backend supports additional rates and longer integer-second durations, but this firmware's two 16,000-frame buffers do not support those larger eligible recordings. The generator rejects fractional-second durations, which would mismatch the backend's integer-second location rule. Expanding the recording capacity requires a reviewed memory/profile change, not merely increasing a setting. Duration must match the location's threshold interval. Longer capture intervals intentionally create coverage gaps and may prevent the configured contiguous recovery rule from completing.
- Two fixed audio slots occupy about 96 KB. New recordings are dropped when those slots are full, retaining the oldest pending recording. The queue and diagnostic counters are RAM-only and disappear on power loss. DMA overflow drops the incomplete recording, records a gap, and restarts capture with startup audio discarded again.
- Retries preserve the same WAV bytes, capture timestamp, session, sequence, and chunk identifier. A 200 duplicate response and a 202 newly stored response release the buffer only after a valid backend acknowledgement. 401/403 pause; 413/422 pause after rejecting incompatible data; 409 rejects the conflicting recording. Bounded network/5xx retries cannot create a new identifier for an old recording. See [core retry rules](../common/README.md).
- HTTPS uses the supplied CA, hostname/IP-SAN validation, and the existing device Bearer credential. Redirects are disabled. The [official HTTP client documentation](https://docs.espressif.com/projects/esp-idf/en/v5.4.3/esp32/api-reference/protocols/esp_http_client.html) describes this API. Certificate bypass is not enabled. The pinned MbedTLS source supports IP-address SAN matching.

Serial output reports saved server recording IDs, HTTP status, queue counts, retries, dropped recordings, time synchronization, DMA gaps, and free heap. It does not print the token, Wi-Fi password, HTTP response body, or original audio. Every successful `saved_audio_id=...` can be inspected using `scripts/check-device.py --audio-id ...` with the private device registration file. A successful upload is not proof of a calibrated acoustic measurement.

## Test and troubleshoot

From the repository root:

```sh
python -m unittest discover -s firmware/esp32/tests -v
firmware/common/run-tests.sh /tmp/urbanecho-firmware-tests
```

If the firmware says **Not provisioned**, generate the private configuration and rebuild. For Wi-Fi failure, check 2.4 GHz coverage, credentials, WPA2 support, and isolation/firewall settings. No UTC sync means no capture: check DNS/NTP before investigating uploads. TLS failures require checking the device clock, server address, certificate SAN, CA file, and certificate validity; never disable certificate verification.

HTTP 401/403 means the saved device credential/identity or enabled state needs repair. HTTP 413/422 means check WAV size, rate, capture date/assignment, and configuration. HTTP 409 means an ID was reused with different content; investigate identity persistence rather than renaming the upload. Reboot after correcting a paused authentication/configuration failure; complete queued recordings in RAM are lost during that reboot and must not be claimed as delivered.

If uploads succeed but no eligible level appears, inspect the server recording status and measurement diagnostics. Silence, clipped samples, interval mismatch, missing physical calibration, or an incorrect I2S channel can all prevent meaningful results. The initial physical measurement is **uncalibrated dBFS**; trustworthy dB SPL needs an actual calibrated reference procedure and validity metadata. Treat runtime RAM headroom, Wi-Fi reconnection, I2S alignment, timestamp error/drift, physical alerts/recovery, and acoustic calibration as pending until exercised on the connected board.
