"""Incident scope never schedules normal recordings or disguises retained results."""
from app.classification_contract import MAPPING_VERSION, MODEL_VERSION
from app.classification_jobs import classification_dict, run_once


def test_new_recording_is_not_waiting_for_per_clip_classification():
    value = classification_dict(None, {"worker_status": "ready", "worker_error": None,
                                      "classification_scope": "incidents"})
    assert value["status"] == "not_requested"
    assert value["queued"] is False
    assert value["automatic_scope"] == "incidents"
    assert value["primary_category"] is None


def test_prior_recording_result_is_retained_and_explicitly_recording_scoped():
    from types import SimpleNamespace
    row = SimpleNamespace(status="completed", result={"primary_category": "voice"},
                          primary_category="voice", classified_at="saved timestamp", error=None, attempts=1)
    value = classification_dict(row, {"worker_status": "ready", "worker_error": None,
                                     "classification_scope": "incidents"})
    assert value["status"] == "completed" and value["primary_category"] == "voice"
    assert value["scope"] == "recording"
    assert value["model_version"] == MODEL_VERSION and value["mapping_version"] == MAPPING_VERSION


def test_incident_scope_does_not_scan_or_claim_recordings(monkeypatch):
    from types import SimpleNamespace
    def forbidden(*args, **kwargs):
        raise AssertionError("Normal recordings must not be scheduled")
    monkeypatch.setattr("app.classification_jobs.enqueue_missing", forbidden)
    monkeypatch.setattr("app.classification_jobs.claim_job", forbidden)
    assert run_once(SimpleNamespace(classification_enabled=True, classification_scope="incidents")) is False


def test_worker_dispatches_only_incident_jobs(monkeypatch):
    from types import SimpleNamespace
    from app import classification_worker, incident_analysis_jobs
    seen = []
    def recording(*args, **kwargs):
        raise AssertionError("Per-recording jobs are disabled")
    monkeypatch.setattr(classification_worker, "run_recording_once", recording)
    monkeypatch.setattr(incident_analysis_jobs, "run_once", lambda settings, **kwargs: seen.append(kwargs) or True)
    assert classification_worker.run_once(SimpleNamespace(classification_scope="incidents"), prefer_oldest=True)
    assert seen == [{"prefer_oldest": True, "prepare_only": False}]


def test_unavailable_model_still_dispatches_incident_audio_preparation(monkeypatch):
    from types import SimpleNamespace
    from app import classification_worker, incident_analysis_jobs
    seen = []
    monkeypatch.setattr(incident_analysis_jobs, "run_once", lambda settings, **kwargs: seen.append(kwargs) or True)
    assert classification_worker.run_once(SimpleNamespace(classification_scope="incidents"), prepare_only=True)
    assert seen == [{"prefer_oldest": False, "prepare_only": True}]


def test_audio_only_preparation_never_dispatches_legacy_recording_inference(monkeypatch):
    from types import SimpleNamespace
    from app import classification_worker
    def forbidden(*args, **kwargs):
        raise AssertionError("Unavailable model must not run recording inference")
    monkeypatch.setattr(classification_worker, "run_recording_once", forbidden)
    assert classification_worker.run_once(SimpleNamespace(classification_scope="recordings"), prepare_only=True) is False
