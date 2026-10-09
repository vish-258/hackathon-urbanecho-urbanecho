#!/bin/sh
# Creates and removes ONLY its own disposable project and volumes.
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo 'Docker is required.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'Python 3 is required to create temporary test secrets.' >&2; exit 1; }
mkdir -p .local
TEST_ENV=$(mktemp .local/test-env.XXXXXX)
TEST_PROJECT="noise-integration-$(date +%s)-$$"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT"
export COMPOSE_FILE=compose.yaml:compose.test.yaml
case "$(uname -m)" in arm64|aarch64) COMPOSE_FILE="$COMPOSE_FILE:compose.amd64.yaml"; export COMPOSE_FILE;; esac
python3 - "$TEST_ENV" <<'PY'
import secrets, sys
from pathlib import Path
values = {'POSTGRES_DB':'noise_monitor_test', 'POSTGRES_USER':'noise_migrate', 'APP_DB_USER':'noise_app'}
values.update({key: secrets.token_hex(32) for key in ('POSTGRES_PASSWORD','APP_DB_PASSWORD','ADMIN_TOKEN')})
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
docker compose --env-file "$TEST_ENV" run --build --rm tests "$@"
