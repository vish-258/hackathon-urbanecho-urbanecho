"""Synthetic calibration exercises the same format gates as physical uploads.

These fixtures prove software eligibility, not acoustic calibration accuracy.
"""
import math
import struct

import pytest

from app.daily_jobs import run_daily_once
from tests.test_integration import SYNTHETIC_CALIBRATION, device, location, metadata, process, upload
from tests.test_pcm_upload_api import headers


pytestmark = pytest.mark.integration


def captured_samples(bits, amplitude):
    width = bits // 8
    value = round(amplitude * (1 << (bits - 1)))
    return b"".join(sample.to_bytes(width, "little", signed=True)
                    for sample in [-value, value] * 8000)


def send_samples(client, dev, bits, amplitude, sequence):
    pcm = captured_samples(bits, amplitude)
    if bits == 16:
        response = client.post("/upload", headers=headers(dev, sequence=sequence), content=pcm)
        assert response.status_code == 200, response.text
    else:
        fmt = struct.pack("<HHIIHH", 1, 1, 16000, 48000, 3, 24)
        body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
        wav = b"RIFF" + struct.pack("<I", len(body)) + body
        response = upload(client, dev, audio=wav, meta=metadata(dev, sequence=sequence))
        assert response.status_code == 202, response.text
    return response.json()["id"]


def report_for(client, admin_headers, settings, location_id):
    params = {"location_id": location_id, "reporting_date": "2026-02-01"}
    requested = client.post("/daily-summaries/generate", headers=admin_headers, json=params)
    assert requested.status_code == 202, requested.text
    assert run_daily_once(settings)
    response = client.get("/daily-summaries", headers=admin_headers, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["report"]["status"] == "completed"
    return response.json()


@pytest.mark.parametrize("bits,calibration_bits", [(16, 16), (24, 24), (24, None)])
def test_calibrated_formats_reach_live_alert_recovery_and_daily_report(
        client, admin_headers, settings, bits, calibration_bits):
    loc = location(client, admin_headers, name="SIMULATED · calibrated format regression",
                   threshold_type="spl_z_leq", threshold_value=60)
    calibration = {**SYNTHETIC_CALIBRATION, "offset_db": 75 - 20 * math.log10(.5)}
    if calibration_bits is not None:
        calibration["pcm_bits"] = calibration_bits
    dev = device(client, admin_headers, loc["id"], calibration=calibration,
                 microphone_model="SIMULATED software-only calibration fixture")
    levels = []
    for sequence, amplitude in enumerate([.5, .05, .05, .05]):
        identifier = send_samples(client, dev, bits, amplitude, sequence)
        process(settings)
        saved = client.get("/audio/" + identifier, headers=admin_headers).json()
        assert saved["status"] == "completed"
        assert saved["audio_format"] == f"wav_pcm_s{bits}le_mono"
        measurement = saved["measurements"][0]
        assert measurement["calibration_status"] == "calibrated"
        assert measurement["quality_status"] == "good"
        assert measurement["value_db"] == pytest.approx(75 if sequence == 0 else 55, abs=.01)
        assert measurement["breach"] is (sequence == 0)
        levels.append(measurement["value_db"])
        incident_response = client.get("/incidents", headers=admin_headers).json()
        assert incident_response["total"] == 1
        assert incident_response["items"][0]["status"] == (
            "active" if sequence == 0 else "resolved" if sequence == 3 else "recovering")

    incident = incident_response["items"][0]
    assert incident["location_id"] == loc["id"]
    assert incident["peak_db"] == pytest.approx(75)
    assert incident["threshold_type"] == "spl_z_leq"
    events = client.get("/events", headers=admin_headers).json()["items"]
    kinds = [event["event_type"] for event in events]
    assert kinds.count("incident.opened") == kinds.count("incident.resolved") == 1
    live = client.get("/locations/status", headers=admin_headers,
                      params={"location_id": loc["id"]}).json()["items"][0]
    assert live["noise_status"] == "normal" and live["data_status"] == "fresh"

    report = report_for(client, admin_headers, settings, loc["id"])
    assert len(report["summaries"]) == 1
    summary = report["summaries"][0]
    assert summary["definition"]["source_kind"] == "simulated"
    assert summary["definition"]["unit"] == "dB SPL (Z)"
    stats = summary["statistics"]
    assert stats["measurement_count"] == 4 and stats["incident_count"] == 1
    assert stats["excluded_count"] == 0 and stats["exclusion_reasons"] == {}
    assert stats["usable_duration_seconds"] == 4 and stats["coverage_status"] == "partial"
    assert stats["average_db"] == pytest.approx(10 * math.log10(sum(10 ** (level / 10) for level in levels) / 4))
    assert stats["minimum_db"] == min(levels) and stats["maximum_db"] == max(levels)
    repeated = report_for(client, admin_headers, settings, loc["id"])
    assert repeated["report"]["id"] == report["report"]["id"]
    assert [row["id"] for row in repeated["summaries"]] == [summary["id"]]
    assert repeated["summaries"][0]["statistics"] == stats


@pytest.mark.parametrize("bits,calibration_bits", [(16, 24), (24, 16)])
def test_wrong_capture_chain_calibration_still_cannot_create_alert_or_usable_summary(
        client, admin_headers, settings, bits, calibration_bits):
    loc = location(client, admin_headers, name="SIMULATED · wrong format calibration",
                   threshold_type="spl_z_leq", threshold_value=60)
    dev = device(client, admin_headers, loc["id"],
                 calibration={**SYNTHETIC_CALIBRATION, "pcm_bits": calibration_bits})
    identifier = send_samples(client, dev, bits, .5, 0)
    process(settings)
    saved = client.get("/audio/" + identifier, headers=admin_headers).json()
    measurement = saved["measurements"][0]
    assert saved["status"] == "completed"
    assert measurement["calibration_status"] == "calibration_required"
    assert measurement["value_db"] is None and measurement["breach"] is False
    assert client.get("/incidents", headers=admin_headers).json()["total"] == 0
    summary = report_for(client, admin_headers, settings, loc["id"])["summaries"][0]
    stats = summary["statistics"]
    assert stats["average_db"] is None and stats["coverage_status"] == "no_data"
    assert stats["measurement_count"] == stats["incident_count"] == 0
    assert stats["excluded_count"] == 1 and stats["usable_duration_seconds"] == 0
