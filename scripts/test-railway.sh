#!/bin/sh
# Build and test Railway packaging locally, touching only disposable test volumes.
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo 'Docker is required.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'Python 3 is required to create temporary test secrets.' >&2; exit 1; }
mkdir -p .local
TEST_ENV=$(mktemp .local/railway-env.XXXXXX)
TEST_PROJECT="noise-railway-$(date +%s)-$$"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT"
export COMPOSE_FILE=deploy/railway/compose.verify.yaml
python3 - "$TEST_ENV" <<'PY'
import secrets, sys
from pathlib import Path
values = {'POSTGRES_DB': 'noise_monitor_railway_test', 'POSTGRES_USER': 'noise_migrate', 'APP_DB_USER': 'noise_app'}
values.update({key: secrets.token_hex(32) for key in ('POSTGRES_PASSWORD', 'APP_DB_PASSWORD', 'ADMIN_TOKEN')})
path = Path(sys.argv[1])
path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
path.chmod(0o600)
PY
# Shell-exported development credentials must not override the test environment.
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD APP_DB_USER APP_DB_PASSWORD ADMIN_TOKEN DATABASE_URL MIGRATION_DATABASE_URL
cleanup() {
  result=$?
  if [ "$result" -ne 0 ]; then docker compose --env-file "$TEST_ENV" logs --tail=80 >&2 || true; fi
  docker compose --env-file "$TEST_ENV" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$TEST_ENV"
  exit "$result"
}
trap cleanup EXIT HUP INT TERM
compose() { docker compose --env-file "$TEST_ENV" "$@"; }
compose up --build -d --wait app

# API readiness checks storage, PostGIS and schema; inspect process identities
# without ever printing environment values or credentials.
compose exec -T --user 10001:10001 app python - <<'PY'
import json, os, time, urllib.error, urllib.request
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import get_settings
from sqlalchemy import create_engine, text
base = 'http://localhost:8087'
with urllib.request.urlopen(base + '/health/ready', timeout=5) as response:
    assert response.status == 200
with urllib.request.urlopen(base + '/demo', timeout=5) as response:
    assert response.status == 200
with urllib.request.urlopen(base + '/app', timeout=5) as response:
    assert response.status == 200
try:
    urllib.request.urlopen(base + '/devices', timeout=5)
    raise AssertionError('Unauthenticated device listing was accepted')
except urllib.error.HTTPError as error:
    assert error.code == 401
try:
    request = urllib.request.Request(base + '/app/session', data=b'{}',
        headers={'Content-Type': 'application/json', 'X-Soundwatch-Local': '1', 'Origin': base})
    urllib.request.urlopen(request, timeout=5)
    raise AssertionError('Public deployment accepted an automatic local session')
except urllib.error.HTTPError as error:
    assert error.code == 403
commands = {}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        command = (entry / 'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, PermissionError):
        continue
    for module in (b'scripts.serve_railway', b'app.worker', b'app.classification_worker', b'uvicorn'):
        if module in command:
            status = (entry / 'status').read_text().splitlines()
            assert next(line for line in status if line.startswith('Uid:')).split()[1:] == ['10001'] * 4
            assert next(line for line in status if line.startswith('Gid:')).split()[1:] == ['10001'] * 4
            if module != b'scripts.serve_railway':
                environment = (entry / 'environ').read_bytes().split(b'\0')
                names = {item.split(b'=', 1)[0] for item in environment}
                assert not names.intersection({b'MIGRATION_DATABASE_URL', b'POSTGRES_USER', b'POSTGRES_PASSWORD'})
            commands[module] = entry.name
assert set(commands) == {b'scripts.serve_railway', b'app.worker', b'app.classification_worker', b'uvicorn'}
assert Path('/data/audio').stat().st_uid == 10001
settings = get_settings()
assert settings.local_browser_access is False
with create_engine(settings.database_url).connect() as connection:
    expected_head = ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    assert connection.execute(text('select version_num from alembic_version')).scalar_one() == expected_head
    assert connection.execute(text('select postgis_version()')).scalar_one().startswith('3.5')
    privileged = connection.execute(text('select rolsuper or rolcreatedb or rolcreaterole or rolreplication from pg_roles where rolname = current_user')).scalar_one()
    assert privileged is False
for _ in range(120):
    request = urllib.request.Request(base + '/classification/status',
        headers={'Authorization': 'Bearer ' + os.environ['ADMIN_TOKEN']})
    with urllib.request.urlopen(request, timeout=5) as response:
        status = json.load(response)
    if status['worker_status'] == 'ready':
        assert status['classification_scope'] == 'incidents' and status['enabled'] is True
        break
    time.sleep(.5)
else:
    raise AssertionError('Classifier did not initialize its verified model')
print('PASS: dynamic port, application, authentication, disabled local access, latest schema, PostGIS, restricted database role, volume ownership, all process identities, child credential filtering and classifier model readiness.')
PY

# Existing fixture exercises actual HTTP uploads, worker processing and incidents.
compose exec -T --user 10001:10001 app python - <<'PY'
import importlib.util
import json
import time
spec = importlib.util.spec_from_file_location('persistence_live', '/app/scripts/persistence-live.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.BASE = 'http://localhost:8087'
fixture.prepare()
incident_id = json.loads(fixture.PATH.read_text())['incident_id']
for _ in range(120):
    analysis = fixture.request(fixture.BASE, fixture.ADMIN, '/incidents/' + incident_id + '/analysis')
    if analysis['status'] == 'completed' and analysis['audio']['available']:
        assert analysis['classification']['primary_category'] is not None
        break
    time.sleep(.5)
else:
    raise AssertionError('Classifier did not produce incident playback and a saved sound estimate')
print('PASS: real incident audio and model classification complete in the combined runtime.')
PY

# Remove and recreate both services while preserving ONLY this project's volumes.
compose down
compose up -d --wait app
compose exec -T --user 10001:10001 app python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location('persistence_live', '/app/scripts/persistence-live.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.BASE = 'http://localhost:8087'
fixture.verify()
PY

# A stopped classifier must stop the API and measurement worker too, so the
# platform can restart every process sharing the one persistent volume.
APP_CONTAINER=$(compose ps -q app)
compose exec -T app python - <<'PY'
import os, signal
from pathlib import Path
for entry in Path('/proc').iterdir():
    if entry.name.isdigit():
        try:
            command = (entry / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError):
            continue
        if b'app.classification_worker' in command:
            os.kill(int(entry.name), signal.SIGTERM)
            break
else:
    raise AssertionError('Classifier was not running')
PY
TEST_EXIT=$(python3 - "$APP_CONTAINER" <<'PY'
import subprocess, sys
result = subprocess.run(['docker', 'wait', sys.argv[1]], check=True, capture_output=True, text=True, timeout=30)
print(result.stdout.strip())
PY
)
test "$TEST_EXIT" = 1 || { echo 'Unexpected classifier exit did not fail the combined service.' >&2; exit 1; }
echo 'PASS: an unexpected classifier exit stops the combined service with status 1.'

# Without the mounted audio volume, startup must fail before accepting traffic.
if docker run --rm --user 0:0 \
    --env RAILWAY_ENVIRONMENT_ID=isolated-verification \
    --env RAILWAY_VOLUME_MOUNT_PATH=/data/audio \
    noise-monitor:railway-test python -m scripts.serve_railway; then
  echo 'Startup incorrectly accepted ephemeral audio storage.' >&2
  exit 1
fi
echo 'PASS: Railway startup refuses missing persistent audio storage.'
echo 'PASS: isolated Railway packaging verification completed; test resources will be removed.'
