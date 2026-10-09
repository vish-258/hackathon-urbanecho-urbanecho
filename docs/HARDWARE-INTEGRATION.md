# UrbanEcho physical device integration

## What is known, and what remains to verify

The user has confirmed an **ESP-WROOM-32 development board with Micro-USB** and an **INMP441 I²S microphone**, with the wiring below. The board is connected to another computer, which can build and flash the C++ firmware. UrbanEcho can continue running on this Mac: the ESP connects directly over Wi-Fi to the Mac's optional device listener. The **ESP and backend Mac** must share a reachable local network; the flashing computer can be elsewhere and is not needed for subsequent uploads. No public tunnel is required.

The exact development-board carrier revision and microphone breakout schematic have not been independently inspected. Use the printed **GPIO labels**, not assumed header positions from an unrelated board diagram. Firmware compilation, successful uploads, and acoustic accuracy are separate checks. Physical capture, Wi-Fi recovery, and acoustic calibration remain pending until the user loads the firmware and performs the checks below. The current software verification results belong in the Step 6 verification report.

## Confirmed wiring

Disconnect board power before checking these connections.

| INMP441 connection | ESP-WROOM-32 development board | Function |
|---|---|---|
| VDD / 3V3 | 3.3 V | Microphone power |
| GND | GND | Common ground |
| SCK / BCLK | GPIO 26 | ESP clock output |
| WS / LRCL | GPIO 25 | ESP word-select output |
| SD | GPIO 33 | ESP audio input |
| L/R | GND | Left audio slot |

GPIO 25, 26, and 33 are general-purpose input/output pins on the ESP32-WROOM-32. They are outside the boot-strapping and flash-pin groups, so this mapping is suitable for I²S through the GPIO matrix. It does not establish any particular carrier's physical header positions. See the [official module datasheet, pin definitions and boot configurations](https://documentation.espressif.com/esp32-wroom-32_datasheet_en.html).

INMP441 uses Philips I²S: 24-bit samples, 32 clocks per slot, 64 clocks per stereo frame; grounded L/R selects the left slot. Do not connect its supply or signals to 5 V. Check the breakout's existing decoupling and pulldown against the [TDK INMP441 datasheet](https://product.tdk.com/system/files/dam/doc/product/sw_piezo/mic/mems-mic/data_sheet/inmp441.pdf). The adapter uses left-channel, 32-bit slots and extracts the upper 24 bits; verify this on the actual assembly before trusting levels. The [Espressif I²S documentation](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32/api-reference/peripherals/i2s.html) distinguishes Philips timing from MSB timing and explains DMA alignment.

## Prepare the backend Mac

Keep Docker and the existing UrbanEcho stack running. The normal web application stays at `http://localhost:8000/app`; it is not made public. A separate optional HTTPS listener shares the same database, recordings, processing worker, and device-upload handlers.

1. Find the Mac's private Wi-Fi/Ethernet IPv4 address in macOS Network settings. Use the address reachable by the ESP, not `localhost`, a public address, or `0.0.0.0`. Keep that address stable with a router reservation if possible.
2. From the project folder, create a private certificate for that address:

   ```sh
   python3 scripts/setup-hardware-tls.py --ip <Mac-private-LAN-IP>
   ```

   This prepares files but starts no server. Existing certificate directories are preserved; the command refuses to overwrite them.

3. When ready to connect hardware, start only the optional listener alongside the existing stack:

   ```sh
   docker compose --env-file .env --env-file .local/hardware-tls/listener.env \
     -f compose.yaml -f compose.amd64.yaml -f compose.hardware.yaml \
     --profile hardware up -d --build device-api
   ```

   Its device address is `https://<Mac-private-LAN-IP>:8443`. Allow this connection in the Mac's firewall if prompted. Router guest isolation can prevent the ESP reaching it even when both devices have internet access.

4. In UrbanEcho **Management**, create or choose a physical test location using its real coordinates and timezone. Keep it separate from locations labelled SIMULATED, SYNTHETIC, or Demo. Start with measurement type **digital dBFS**, a one-second interval, and a digital threshold appropriate to the observed microphone levels. These digital values are not calibrated environmental SPL.
5. Copy the location UUID from its location-page URL, then register the physical device:

   ```sh
   python3 scripts/register-hardware.py --admin-env-file .env \
     --location-id <physical-location-UUID> \
     --endpoint https://<Mac-private-LAN-IP>:8443 \
     --board-profile esp32-wroom-32-inmp441
   ```

The command reads the administrator credential privately; do not paste it into chat. It creates `.local/hardware-device.json` with owner-only permissions, the registered device UUID and token, and the confirmed pin profile. Calibration stays empty. Repeating the same command reuses that device and its private configuration; it never silently rotates credentials or moves the device. Preserve this file: the server cannot recover the original token from its stored hash.

The listener is opt-in and is **not started by normal application startup**. To stop it, use the same Compose arguments with `stop device-api`. Do not delete application volumes.

The generated server certificate lasts **365 days** and names the selected Mac IP address. Before expiry, or if that address changes, prepare a new directory with `--directory .local/hardware-tls-next`, update the listener's `--env-file` to that directory, and rebuild the device configuration with its new `ca.crt` and endpoint. Preserve the old files until the replacement works; do not turn off certificate checks to work around an address or expiry mismatch.

## Configure and flash on the other computer

Use the complete `firmware/esp32` C++ project and the adjacent `firmware/common` directory; this is an ESP-IDF project, not a standalone Arduino sketch. The target is **esp32**, using **ESP-IDF v5.4.3**. Follow the firmware directory's README for the exact supported build environment and its compilation status.

Transfer the project source, `.local/hardware-device.json`, and **only** `.local/hardware-tls/ca.crt` privately to the flashing computer. Never transfer the backend `.env`, server private key, or CA signing key to the board. Avoid emailing or committing the device configuration, since it contains the device credential.

Edit the private JSON locally:

- Set `wifi.ssid` and `wifi.password` for the ESP's 2.4 GHz Wi-Fi network.
- Check `endpoint` against the Mac's reachable HTTPS address.
- Confirm the physical pin labels match the table, then set `board.wiring_verified` to `true`.
- Keep the initial sample rate at 16,000 Hz and recording duration/interval at one second. The initial fixed buffers hold two one-second recordings. Longer recordings or higher rates require deliberate memory/buffer configuration and a matching location interval.

The optional `clock.sntp_server` defaults to `pool.ntp.org`; choose a reachable local time server if the ESP's network has no internet access. The current adapter supports password-protected WPA2-compatible networks; open/enterprise Wi-Fi requires another provisioning approach.

Generate the private build header on the flashing computer, from the copied project root:

```sh
python3 firmware/esp32/configure.py --config .local/hardware-device.json \
  --ca .local/hardware-tls/ca.crt
python -m esptool --chip esp32 --port <board-serial-port> flash_id
idf.py -C firmware/esp32 set-target esp32
idf.py -C firmware/esp32 build
idf.py -C firmware/esp32 -p <board-serial-port> flash monitor
```

Run these commands from an activated ESP-IDF v5.4.3 environment. The `flash_id` command reads the chip's flash information before writing firmware. The supplied profile assumes **4 MB flash**; if the detected size differs, select the correct flash size and a compatible partition table with `idf.py -C firmware/esp32 menuconfig` before building/flashing. Do not assume capacity from the carrier's appearance.

`configure.py` generates an ignored private header; never publish it or a configured firmware binary. Select the port belonging to the board on the **flashing computer**. Carrier-specific USB bridge/drivers and automatic boot-button behavior still depend on that board. A successful build or flash is not yet a microphone or Wi-Fi test. Do not flash the `--compile-check` configuration: its credentials and address are deliberately fake.

## Backend recording contract

| Item | Existing requirement |
|---|---|
| Upload | `POST /audio`, device `Authorization: Bearer` credential |
| Body | Multipart `metadata` JSON plus `file` containing the original WAV |
| Format | Conventional RIFF/WAVE, PCM format 1, mono, signed **packed 24-bit little-endian**, 3 bytes/sample; 16-byte `fmt` chunk |
| Supported rates | Defaults: 16,000 / 32,000 / 44,100 / 48,000 Hz |
| Recording length | Positive; default maximum 60 seconds, administrator-configurable up to 600 seconds; eligible measurements must match the location rule's interval |
| Upload size | Default audio-file limit 20,000,000 bytes; the backend configuration can lower it |
| Initial firmware size | One second at 16 kHz: 48,000 audio bytes, normally 48,044 WAV bytes, plus multipart metadata |
| Metadata | `device_id` UUID; `chunk_id`; `session_id`; nonnegative signed-64-bit `sequence`; timezone-aware `captured_at` for the **first captured sample** |
| Identifier syntax | `chunk_id` and `session_id`: 1–128 characters from letters, digits, `_ . : -` |
| Success | `202` means accepted and queued; inspect the returned audio UUID until processing completes |
| Identical retry | `200` with `duplicate: true`; keep the same bytes, capture time, identifiers, and sequence |
| Conflicting retry | `409` means an existing recording identifier was reused with different content; do not invent a new ID to hide the conflict |

Raw 32-bit DMA words, PCM16, stereo files, RF64, and extensible WAV are not this backend's upload format. Firmware performs the I²S-to-packed-PCM24 conversion; the server calculates the sound level.

Time synchronization must succeed before eligible recording begins. Capture timestamps remain unchanged during upload retries and are converted to a location's calendar day on the server. The existing live policy excludes future captures and captures more than 120 seconds old by default; delayed recordings can still contribute to history and daily summaries without inventing current-noise alerts. The stale indicator defaults to 30 seconds without fresh eligible readings.

The firmware's bounded queue and retry behavior are described in its README. Retried recordings retain their identity. Overflow, capture failure, rejected recordings, and exhausted retries are counted in serial diagnostics; missing sound is not replaced with fabricated silence. The initial queue is in RAM, so an unexpected power loss loses unacknowledged recordings; host software tests do not establish loss-free hardware capture.

## Verify the physical flow

Keep UrbanEcho open on the backend Mac. After flashing, perform these checks and save their observations separately from simulator results:

1. Confirm Wi-Fi connection and clock synchronization in the board's serial monitor without printing credentials. Compare the first capture time with an independent clock.
2. Confirm an upload receives an audio UUID, then run this read-only check on the backend Mac:

   ```sh
   python3 scripts/check-device.py --admin-env-file .env --verify-audio
   ```

   Check the device-to-location match, sample rate, duration, stored-file checksum, processing status, measured type, quality, and capture-to-receipt delay. If processing is still pending, inspect the UUID explicitly:

   ```sh
   python3 scripts/check-device.py --audio-id <accepted-audio-UUID> \
     --ca-file .local/hardware-tls/ca.crt --verify-audio
   ```

3. Check the same location on the map and its latest readings. With an appropriate digital threshold, confirm a level strictly above the threshold creates one saved incident and visible opening notification. Sustained excessive recordings update that incident. Equality is normal; the default recovery rule requires three consecutive eligible normal recordings with contiguous capture windows and sequence numbers.
4. Temporarily disconnect the ESP's Wi-Fi. Confirm that the application becomes stale; staleness must not resolve an incident. Reconnect and examine retry/drop counters. Any repeated upload must reuse its original identifier and produce no duplicate recording or incident. A sequence or time gap resets recovery continuity.
5. In **Daily reports**, select this physical location and the capture's local date, then **Generate/recalculate summary**. Confirm the report uses recorded digital measurements and shows the actual duration/coverage. A few test recordings should produce partial coverage, not a complete monitored day.

The checker only reads saved data; it does not recalculate a report. Daily summaries are location-wide, so inspect the recording's historical location when a device has been reassigned.

## Calibration and result meaning

Transmission success means the server received and processed the saved audio. A checksum match means the original stored bytes match their saved digest. Neither establishes microphone sensitivity or trustworthy physical sound pressure.

Uncalibrated `dbfs_rms` is useful for connection and processing tests and is labelled digital dBFS. For `spl_z_leq`, measure the actual assembled microphone against an appropriate traceable acoustic reference, using the chosen sample rate and server's DC-removed RMS definition. Determine `offset_db = reference_SPL - measured_digital_dbfs`, then independently validate it at additional levels and frequencies. Record the calibration method, sample rate, version, calibration time, expiry, Z weighting, and mono channel policy in the device's server configuration. Do not copy synthetic calibration or infer a trusted offset from nominal microphone sensitivity.

Without applicable calibration, a location configured for SPL can still store the original audio and digital diagnostic level, but its eligible SPL value, alerts, and SPL daily statistics remain unavailable. This microphone-plus-software path is not claimed to be a certified sound-level meter or a calibrated dBA measurement system.

## Troubleshooting

| Symptom | Check |
|---|---|
| No flashing port | Check the other computer, a data-capable Micro-USB cable, board power, and the actual USB bridge driver; do not select Bluetooth ports. |
| Constant zero, clipping, or implausible level | Confirm 3.3 V/common ground, SCK26/WS25/SD33, grounded L/R and left slot, Philips timing, 32-bit slots, upper-24-bit conversion, and microphone startup discard. |
| Wi-Fi fails | Use the correct 2.4 GHz network and privately entered credentials; check access-point isolation and signal strength. |
| HTTPS fails | The certificate must cover the Mac's exact address; use its trusted `ca.crt`, correct clock, running listener, reachable port 8443, and firewall permission. Never disable verification. |
| Timestamp invalid or no live alerts | Wait for synchronization; compare capture start with UTC; check future/late status and continuous sequence/timing. |
| `401` / `403` | Use this registered device's token and UUID; verify the device is enabled. The administrator token is not a device-upload credential. |
| `413` / `422` | Check duration, file size, exact PCM24 WAV fields, complete samples, timezone-aware metadata, and historical assignment. |
| `409` | Preserve the original bytes and metadata on retry; inspect identifier generation instead of resubmitting changed data under the same ID. |
| `202` but no reading | Inspect that audio UUID; ensure the existing worker is running and read the processing/quality/calibration diagnostic. |
| No recovery | Check the configured recovery count; clipping, silence, stale data, sequence gaps, interval mismatch, and new sessions cannot count as uninterrupted normal evidence. |
| Empty/stale daily report | Choose the location's local capture date and recalculate after completed processing. Missing recordings remain missing. |

The hardware profile and code prepare the real path; completing this checklist with the physical assembly is still required before marking physical integration verified.
