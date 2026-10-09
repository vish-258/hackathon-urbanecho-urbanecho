#!/bin/sh
# Real Docker persistence check in a separate disposable Compose project.
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo 'Docker is required.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'Python 3 is required to create temporary test secrets.' >&2; exit 1; }
mkdir -p .local
TEST_ENV=$(mktemp .local/persistence-env.XXXXXX)
TEST_PROJECT="noise-persistence-$(date +%s)-$$"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT"
export COMPOSE_FILE=compose.yaml:compose.test.yaml
case "$(uname -m)" in arm64|aarch64) COMPOSE_FILE="$COMPOSE_FILE:compose.amd64.yaml"; export COMPOSE_FILE;; esac
python3 - "$TEST_ENV" <<'PY'
import secrets, sys
from pathlib import Path
values={'POSTGRES_DB':'noise_monitor_test','POSTGRES_USER':'noise_migrate','APP_DB_USER':'noise_app'}
values.update({key:secrets.token_hex(32) for key in ('POSTGRES_PASSWORD','APP_DB_PASSWORD','ADMIN_TOKEN')})
path=Path(sys.argv[1]); path.write_text(''.join(f'{key}={value}\n' for key,value in values.items())); path.chmod(0o600)
PY
# Shell-exported development credentials must not override the disposable test env file.
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD APP_DB_USER APP_DB_PASSWORD ADMIN_TOKEN DATABASE_URL MIGRATION_DATABASE_URL
cleanup() {
  result=$?
  if [ "$result" -ne 0 ]; then docker compose --env-file "$TEST_ENV" logs --tail=100 >&2 || true; fi
  docker compose --env-file "$TEST_ENV" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$TEST_ENV"
  exit "$result"
}
trap cleanup EXIT HUP INT TERM
compose() { docker compose --env-file "$TEST_ENV" "$@"; }
compose up --build -d --wait api worker
compose exec -T api python - <<'PY'
import hashlib, json, os
from pathlib import Path
from scripts.seed import request
from scripts.simulate import pcm24_wav, upload
base='http://localhost:8000'; admin=os.environ['ADMIN_TOKEN']
location=request(base,admin,'/locations',dict(name='Persistence fixture',latitude=12.9716,longitude=77.5946,timezone='UTC',threshold_value=75,threshold_type='spl_z_leq',interval_seconds=1))
device=request(base,admin,'/devices',dict(location_id=location['id']))
audio=pcm24_wav(16000,1,.5)
status,accepted=upload(base,device,dict(device_id=device['id'],chunk_id='persistence-0',captured_at='2026-02-01T12:00:00Z',session_id='persistence',sequence=0),audio)
assert status == 202
Path('/data/audio/persistence-fixture.json').write_text(json.dumps(dict(chunk_id=accepted['id'],location_id=location['id'],device_id=device['id'],sha256=hashlib.sha256(audio).hexdigest())))
print('Created committed database rows and original audio.')
PY
compose exec -T api python -m scripts.persistence-live prepare
# Ordinary down removes containers/network but preserves named volumes.
compose down
compose up -d --wait api worker
compose exec -T api python - <<'PY'
import hashlib,json,os,time,urllib.request
from pathlib import Path
from scripts.seed import request
base='http://localhost:8000'; admin=os.environ['ADMIN_TOKEN']
fixture=json.loads(Path('/data/audio/persistence-fixture.json').read_text())
assert request(base,admin,'/locations/'+fixture['location_id'])['id']==fixture['location_id']
assert request(base,admin,'/devices/'+fixture['device_id'])['id']==fixture['device_id']
for attempt in range(30):
    chunk=request(base,admin,'/audio/'+fixture['chunk_id'])
    if chunk['status']=='completed': break
    time.sleep(1)
assert chunk['status']=='completed',chunk
req=urllib.request.Request(base+'/audio/'+fixture['chunk_id']+'/file',headers={'Authorization':'Bearer '+admin})
with urllib.request.urlopen(req,timeout=10) as response: audio=response.read()
assert hashlib.sha256(audio).hexdigest()==fixture['sha256']
assert request(base,admin,'/measurements')['total']==4
assert request(base,admin,'/incidents?device_id='+fixture['device_id'])['total']==0
print('PASS: locations, devices, original bytes, jobs and measurements survived container recreation.')
PY

compose exec -T api python -m scripts.persistence-live verify
