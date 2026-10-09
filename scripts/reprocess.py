#!/usr/bin/env python3
"""Operator-only local reprocessing; creates immutable historical results.

Run inside the trusted worker container. This command does not accept fabricated
measurements or mutate uploaded originals. By default it uses the calibration
captured at ingestion. --calibration-json can specify a genuine replacement
calibration file; validate it using the same schema as device registration.
"""
from __future__ import annotations
import argparse
import json
import re
import uuid

from sqlalchemy import select
from app.config import get_settings
from app.db import SessionLocal
from app.evaluation import evaluate_measurement, persist_measurement
from app.events import lock_event_clock
from app.models import AudioChunk, Device
from app.processing import calculate_measurement
from app.schemas import Calibration


def reprocess(audio_id: uuid.UUID, version: str, calibration: dict | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", version) or version in {"initial", "legacy-v1"}:
        raise ValueError("result version must be a distinct non-reserved identifier of 1–100 characters")
    settings = get_settings()
    with SessionLocal() as session:
        chunk = session.get(AudioChunk, audio_id)
        if chunk is None:
            raise ValueError("unknown audio recording")
        session.expunge(chunk)
    if calibration is not None:
        chunk.calibration = Calibration.model_validate(calibration).model_dump(mode="json")
    result = calculate_measurement(chunk, settings)
    # The algorithm identity remains intrinsic. The supplied version identifies
    # this additional historical result, including any replacement calibration.
    with SessionLocal() as session, session.begin():
        lock_event_clock(session)
        session.execute(select(Device.id).where(Device.id == chunk.device_id).with_for_update())
        measurement = persist_measurement(session, chunk, result, result_version=version, is_reprocessing=True)
        evaluation = evaluate_measurement(session, measurement, settings=settings)
        return {"measurement_id": str(measurement.id), "result_version": measurement.result_version,
                "evaluation_status": evaluation.status, "live": evaluation.live}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_id", type=uuid.UUID)
    parser.add_argument("--version", required=True)
    parser.add_argument("--calibration-json", help="Path to genuine calibration JSON, never a guessed microphone offset")
    args = parser.parse_args()
    calibration = None
    if args.calibration_json:
        with open(args.calibration_json, encoding="utf-8") as stream:
            calibration = json.load(stream)
    print(json.dumps(reprocess(args.audio_id, args.version, calibration)))


if __name__ == "__main__":
    main()
