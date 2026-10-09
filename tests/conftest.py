"""Integration fixtures: require an explicitly named, disposable PostGIS test DB."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope='session')
def integration_environment(tmp_path_factory):
    application_url = os.environ.get('TEST_DATABASE_URL')
    migration_url = os.environ.get('MIGRATION_DATABASE_URL')
    if not application_url or not migration_url:
        pytest.skip('Set TEST_DATABASE_URL and MIGRATION_DATABASE_URL to a dedicated PostGIS test database.')
    app_url, admin_url = make_url(application_url), make_url(migration_url)
    if not app_url.database or not (app_url.database.endswith('_test') or app_url.database.startswith('test_')):
        pytest.fail('Refusing destructive integration tests: database name must end in _test or begin with test_.')
    if (app_url.host, app_url.port, app_url.database) != (admin_url.host, admin_url.port, admin_url.database):
        pytest.fail('Test application and migration connections must target the same dedicated database.')
    if app_url.username == admin_url.username:
        pytest.fail('Integration tests require separate restricted application and migration roles.')
    os.environ['DATABASE_URL'] = application_url
    os.environ['ADMIN_TOKEN'] = os.environ.get('ADMIN_TOKEN', 'synthetic-test-admin-' + 'x' * 48)
    os.environ['AUDIO_ROOT'] = str(tmp_path_factory.mktemp('audio'))
    os.environ['MAX_UPLOAD_BYTES'] = '200000'
    os.environ['MAX_DURATION_SECONDS'] = '2'
    os.environ['JOB_RETRY_BASE_SECONDS'] = '0.01'
    os.environ['DB_RETRY_DELAY_SECONDS'] = '0'
    from app.config import get_settings
    from app.db import get_engine
    get_settings.cache_clear()
    get_engine.cache_clear()
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config('alembic.ini'), 'head')
    privileged = create_engine(migration_url)
    yield {'privileged': privileged, 'settings': get_settings(), 'root': Path(os.environ['AUDIO_ROOT'])}
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()
    privileged.dispose()


@pytest.fixture
def fake_clock(monkeypatch):
    from app import clock
    class ControlledClock:
        value = datetime(2026, 2, 1, 12, 0, 30, tzinfo=timezone.utc)
        def now(self):
            return self.value
        def set(self, value):
            self.value = value
        def advance(self, seconds):
            self.value += timedelta(seconds=seconds)
    controlled = ControlledClock()
    monkeypatch.setattr(clock, 'now', controlled.now)
    return controlled


@pytest.fixture(autouse=True)
def isolated_database(request):
    if request.node.get_closest_marker('integration') is None:
        yield
        return
    environment = request.getfixturevalue('integration_environment')
    controlled = request.getfixturevalue('fake_clock')
    with environment['privileged'].begin() as connection:
        connection.execute(text('TRUNCATE recording_group_scan_state, classification_scan_state, processing_jobs, measurements, incidents, audio_chunks, devices, locations CASCADE'))
        connection.execute(text('UPDATE event_clock SET last_position=0, epoch=:epoch, created_at=:now WHERE id=1'),
                           {'epoch': uuid.uuid4(), 'now': controlled.now()})
    for child in environment['root'].iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    yield


@pytest.fixture
def settings(integration_environment):
    return integration_environment['settings']


@pytest.fixture
def admin_headers(settings):
    return {'Authorization': f'Bearer {settings.admin_token}'}


@pytest.fixture
def client(integration_environment):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(), raise_server_exceptions=False) as instance:
        yield instance


@pytest.fixture
def db(integration_environment):
    from app.db import SessionLocal
    with SessionLocal() as session:
        yield session


@pytest.fixture
def privileged(integration_environment):
    return integration_environment['privileged']
