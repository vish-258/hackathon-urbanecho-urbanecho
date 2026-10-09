# Arduino device IDs and PCM integration

This update adapts the supplied ESP32/INMP441 sender to UrbanEcho. The board sends its existing code, such as `UE-001`; the server stores the geographic mapping. The old standalone Python test server is no longer needed for this flow. The existing PCM24 `/audio` API and simulator remain supported.

## Register the mapping once

1. Open the local application, **Management → Locations**, and create the actual place with its coordinates and timezone if it does not exist.
2. Open **Management → Devices**. Enter the code physically configured on the board, select the location, and register. The table shows **Device ID → location → coordinates**.
3. Save the device credential displayed once. Put that credential and the matching code into the sketch's private settings file. The app continues to open locally without asking for an administrator token; this separate device credential authenticates uploads.
4. For a moved device, use **Edit mapping**. No firmware location change is necessary. Older recordings, including late uploads, retain the assignment valid at capture time.

Codes are case-sensitive, unique, and use 1–32 ASCII letters, numbers, `_` or `-`. A 32-character hexadecimal code is reserved for UUID compatibility. Existing UUID-based devices continue working without changes. Unknown IDs are rejected: receiving a new ID does not silently create a device or invent its location. A code is immutable after registration; its location remains editable.

The `devices` table holds `external_id` and its current location reference; `locations` holds the coordinates/timezone, and `device_assignments` holds historical assignments. The additive `0005_device_external_id` migration preserves existing device IDs, credentials and stored history. API registration accepts `external_id` on `POST /devices`; location changes use the existing revision-checked `PATCH /devices/{internal_uuid}`.

## Load the updated program

Use [the Arduino sketch and complete setup instructions](../firmware/arduino-pcm/UrbanEchoPCM/README.md). Copy the whole `UrbanEchoPCM` folder to the computer connected to the board. Open `UrbanEchoPCM.ino`, copy `config.example.h` to `privateconfig.h`, and fill that private file locally. No real Wi-Fi password or device credential is shipped.

Confirmed wiring is INMP441 **VDD→3.3 V, GND→GND, SCK→GPIO26, WS→GPIO25, SD→GPIO33, L/R→GND**. The module is classic ESP-WROOM-32; the exact carrier revision is still unconfirmed. Arduino-ESP32 **3.3.8**, generic **ESP32 Dev Module**, is the compilation target; confirm the board and flash settings before uploading.

The default capture is **one second**, replacing the prototype's half-second chunks to match the application's one-second threshold rule. The firmware preserves the prototype's gain and DC blocker. It uses two bounded buffers, reports dropped recordings, synchronizes UTC, and retries the same audio and identifiers. A long network outage cannot be buffered fully.

## Endpoint changes

Set the host to the **computer running UrbanEcho**, reachable from the ESP's Wi-Fi network. The computer flashing the board can be different. `localhost` on the ESP is not the backend Mac, and the original example IP must not be assumed to identify this server.

| Endpoint | Contract |
|---|---|
| `GET /ping` | Public connectivity response `{"pong":true}`; does not prove processing readiness. |
| `POST /text` | Device bearer credential plus `X-Device-ID`; diagnostic body up to 1,000 bytes. Echoed, not printed or saved as noise data. |
| `POST /upload` | Device credential plus headers below; raw PCM is wrapped into an original WAV and queued through existing processing. |
| `GET /recordings/{key}.wav` | Authenticated, bounded export of an ordered, contiguous PCM session. Use the returned URL, not a filename guessed from the external ID. |

Each `/upload` request requires:

```text
Authorization: Bearer <the registered device credential>
X-Device-ID: UE-001
X-Session: <stable random identifier for this boot, 1–32 safe characters>
X-Seq: <nonnegative sequence number, unchanged on retry>
X-Captured-At: <capture START in UTC, for example 2026-10-09T12:00:00.123456Z>
Content-Type: application/octet-stream
```

The body is **signed little-endian PCM16, mono, 16,000 samples/second**, without a WAV header. One second contains **32,000 bytes**. The raw endpoint accepts at most 1,000,000 bytes and also obeys configured upload/duration limits. Format and sample rate are fixed by this endpoint, not inferred from headers. Do not send the I²S 32-bit slots directly.

HTTP **200** and `{ok, seq, id, status, duplicate, recording_key, recording_url}` acknowledge a saved upload or identical retry; measurement processing may still be pending. Reusing an identity with different audio or metadata returns **409**, not another recording. Duplicate protection is stored in PostgreSQL and survives server restart. The old test server's `{ok,seq}` reply does not meet this stronger acknowledgement contract.

Session export refuses missing sequence numbers, gaps or overlaps instead of removing missing time or inserting silence. It starts at sequence zero and is limited by the server's maximum export size; use individual `/audio/{id}/file` downloads for long or incomplete sessions. Normal storage/processing does not depend on successful session export.

## Local network connection

The ordinary application remains bound to **127.0.0.1:8000** and cannot be reached by an ESP elsewhere on the LAN. The optional device-only HTTPS service is available through `compose.hardware.yaml` on an explicitly selected private LAN IP at **8443**. It exposes device upload/download endpoints, not the management application. It is not enabled automatically by this source update.

Follow [the existing local HTTPS guide](HARDWARE-INTEGRATION.md) to generate the certificate for the backend Mac's current LAN IP and start this service. The short sequence is:

```sh
python3 scripts/setup-hardware-tls.py --ip YOUR_BACKEND_MAC_LAN_IP
# If TLS files already exist, reuse them only while their IP/certificate is valid.
# On this Apple Silicon development Mac, include compose.amd64.yaml:
docker compose --env-file .env --env-file .local/hardware-tls/listener.env \
  -f compose.yaml -f compose.amd64.yaml -f compose.hardware.yaml \
  --profile hardware up -d --build device-api
```

Use `UE_USE_HTTPS=true`, `UE_PORT=8443`, the same IP as `UE_HOST`, and copy only the **public** `.local/hardware-tls/ca.crt` into `UE_CA_CERT`. Keep keys private. No cloud server or public tunnel is required. The ESP must have routing to this computer; separate isolated Wi-Fi networks or client isolation may prevent it. The Mac firewall must allow the selected listener.

## Measurement meaning and checks

Native PCM16 is retained as PCM16 and measured with its actual full-scale divisor. It is never padded and presented as original PCM24. A physical, uncalibrated recording produces **dBFS digital level**, not trustworthy dB SPL. For an initial bench alert test, select a **dBFS** threshold and a **one-second** interval at the mapped location. A positive physical SPL threshold is not comparable to a negative digital dBFS value.

SPL requires calibration of the complete microphone/gain/filter chain, explicitly declaring `pcm_bits: 16`. Existing PCM24 calibration is not automatically transferred. Changing firmware gain or filtering requires reassessing calibration. Valid physical measurements remain separate from simulated results; daily reports preserve measurement definition and coverage gaps.

After flashing, check in order: serial Wi-Fi/time readiness; authenticated saved recording ID; the mapped location's latest reading and original audio; an eligible breach and configured recovery; a repeated request with no extra recording; then today's daily report. A failed/late/invalid recording must not manufacture a current alert. Receiving bytes and compiling firmware do **not** establish microphone accuracy or physical-device success.

See the [verification report](PCM-INTEGRATION-VERIFICATION.md) for the checks actually performed. Physical flashing, Wi-Fi operation, timing accuracy, real sound capture and acoustic calibration remain pending on the user's board.
