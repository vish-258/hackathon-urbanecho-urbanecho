"""Separate optional model process: python -m app.classification_worker."""
import logging
import signal
import threading

from sqlalchemy.exc import SQLAlchemyError

from app.classification_jobs import record_worker_status, run_once as run_recording_once
from app.config import get_settings
from app.db import wait_for_database

log = logging.getLogger(__name__)


def run_once(settings, *, prefer_oldest=False, prepare_only=False):
    if settings.classification_scope == "incidents":
        from app.incident_analysis_jobs import run_once as run_incident_once
        return run_incident_once(settings, prefer_oldest=prefer_oldest, prepare_only=prepare_only)
    if prepare_only:
        return False
    return run_recording_once(settings, prefer_oldest=prefer_oldest)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    initialized = False
    iteration = 0
    while not stop.is_set():
        try:
            wait_for_database()
            if not settings.classification_enabled:
                record_worker_status("disabled", "Automatic sound classification is disabled.")
                stop.wait(30)
                continue
            if not initialized:
                record_worker_status("starting")
                try:
                    from app.classification import prepare_model
                    prepare_model(model_path=settings.classification_model_path)
                    initialized = True
                except Exception as error:
                    log.warning("Classification model unavailable (%s); analysis jobs are preserved", type(error).__name__)
                    record_worker_status("unavailable", "The sound classification model is unavailable; saved recordings are waiting.")
                    # Verified incident playback must not depend on model startup.
                    run_once(settings, prefer_oldest=iteration % 5 == 4, prepare_only=True)
                    iteration += 1
                    stop.wait(30)
                    continue
            record_worker_status("ready")
            # New incidents get priority; retained historical incidents also progress.
            worked = run_once(settings, prefer_oldest=iteration % 5 == 4)
            iteration += 1
            if not worked:
                stop.wait(settings.classification_poll_seconds)
        except SQLAlchemyError:
            log.warning("Classification database unavailable; durable work will resume")
            stop.wait(settings.classification_poll_seconds)


if __name__ == "__main__":
    main()
