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
import json, os, urllib.error, urllib.request
from pathlib import Path
from app.config import get_settings
from sqlalchemy import create_engine, text
base = 'http://localhost:8087'
with urllib.request.urlopen(base + '/health/ready', timeout=5) as response:
    assert response.status == 200
with urllib.request.urlopen(base + '/demo', timeout=5) as response:
    assert response.status == 200
try:
    urllib.request.urlopen(base + '/devices', timeout=5)
    raise AssertionError('Unauthenticated device listing was accepted')
except urllib.error.HTTPError as error:
    assert error.code == 401
commands = {}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        command = (entry / 'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, PermissionError):
        continue
    for module in (b'scripts.serve_railway', b'app.worker', b'uvicorn'):
        if module in command:
            status = (entry / 'status').read_text().splitlines()
            assert next(line for line in status if line.startswith('Uid:')).split()[1:] == ['10001'] * 4
            assert next(line for line in status if line.startswith('Gid:')).split()[1:] == ['10001'] * 4
            if module != b'scripts.serve_railway':
                environment = (entry / 'environ').read_bytes().split(b'\0')
                names = {item.split(b'=', 1)[0] for item in environment}
                assert not names.intersection({b'MIGRATION_DATABASE_URL', b'POSTGRES_USER', b'POSTGRES_PASSWORD'})
            commands[module] = entry.name
assert set(commands) == {b'scripts.serve_railway', b'app.worker', b'uvicorn'}
assert Path('/data/audio').stat().st_uid == 10001
settings = get_settings()
with create_engine(settings.database_url).connect() as connection:
    assert connection.execute(text('select version_num from alembic_version')).scalar_one() == '0003_legacy_audio_guard'
    assert connection.execute(text('select postgis_version()')).scalar_one().startswith('3.5')
    privileged = connection.execute(text('select rolsuper or rolcreatedb or rolcreaterole or rolreplication from pg_roles where rolname = current_user')).scalar_one()
    assert privileged is False
print('PASS: dynamic port, API readiness, demo, authentication, schema, PostGIS, restricted database role, volume ownership, process identities and child credential filtering.')
PY

# Existing fixture exercises actual HTTP uploads, worker processing and incidents.
compose exec -T --user 10001:10001 app python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location('persistence_live', '/app/scripts/persistence-live.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.BASE = 'http://localhost:8087'
fixture.prepare()
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

# A stopped worker must stop the API too, so the platform can restart the service.
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
        if b'app.worker' in command:
            os.kill(int(entry.name), signal.SIGTERM)
            break
else:
    raise AssertionError('Worker was not running')
PY
TEST_EXIT=$(python3 - "$APP_CONTAINER" <<'PY'
import subprocess, sys
result = subprocess.run(['docker', 'wait', sys.argv[1]], check=True, capture_output=True, text=True, timeout=30)
print(result.stdout.strip())
PY
)
test "$TEST_EXIT" = 1 || { echo 'Unexpected worker exit did not fail the combined service.' >&2; exit 1; }
echo 'PASS: an unexpected worker exit stops the combined service with status 1.'

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
