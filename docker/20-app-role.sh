#!/bin/sh
set -eu
: "${APP_DB_USER:?APP_DB_USER must be configured}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be configured}"
if [ "$APP_DB_USER" = "$POSTGRES_USER" ]; then
    echo "Application and migration roles must differ" >&2
    exit 1
fi
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 --set=app_user="$APP_DB_USER" \
    --set=app_password="$APP_DB_PASSWORD" --set=database="$POSTGRES_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', :'database') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database', :'app_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Full_Version();
SQL
