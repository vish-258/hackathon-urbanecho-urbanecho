"""Migrate, verify private object storage, then supervise the sleeping free demo."""
from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url

from scripts.migration_config import migration_search_path
from scripts.serve_railway import APP_GID, APP_UID, child_environment, parse_port, run_services, service_commands
from scripts.setup_supabase import app_role

LOGGER = logging.getLogger("noise.render")
CACHE_ROOT = Path("/tmp/urbanecho-audio-cache")


def resolved_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Accept separate secret fields without interpolating passwords into URLs."""
    result = dict(environment)
    if not result.get("DATABASE_URL") or not result.get("MIGRATION_DATABASE_URL"):
        host = result.get("SUPABASE_POOLER_HOST", "")
        project = result.get("SUPABASE_PROJECT_REF", "")
        if not host.endswith(".pooler.supabase.com") or not project.isalnum():
            raise ValueError("Supabase connection fields require a pooler host and project reference")
        role = app_role(result)
        for target, user, secret in (("DATABASE_URL", role, "APP_DB_PASSWORD"),
                                     ("MIGRATION_DATABASE_URL", "postgres", "SUPABASE_DB_PASSWORD")):
            if not result.get(target):
                password = result.get(secret, "")
                if len(password) < (32 if secret == "APP_DB_PASSWORD" else 1):
                    raise ValueError("Required database password field is missing or too short")
                result[target] = URL.create("postgresql+psycopg", username=f"{user}.{project}",
                    password=password, host=host, port=5432, database="postgres",
                    query={"sslmode": "require"}).render_as_string(hide_password=False)
    return result


def validate_environment(environment: Mapping[str, str]):
    if environment.get("LOCAL_BROWSER_ACCESS", "").lower() not in {"false", "0"}:
        raise ValueError("Render requires LOCAL_BROWSER_ACCESS=false")
    if environment.get("AUDIO_STORAGE_BACKEND") != "supabase":
        raise ValueError("Render requires durable Supabase audio storage")
    root = Path(environment.get("AUDIO_ROOT", ""))
    if root != CACHE_ROOT or root.is_symlink() or root.resolve() != CACHE_ROOT.resolve():
        raise ValueError("Render requires its separate temporary audio cache path")
    endpoint = urlsplit(environment.get("SUPABASE_URL", ""))
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
        raise ValueError("SUPABASE_URL must be an HTTPS project endpoint")
    for name in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_STORAGE_BUCKET", "ADMIN_TOKEN"):
        if not environment.get(name):
            raise ValueError("Render requires private storage configuration and an administrator credential")
    if len(environment["ADMIN_TOKEN"]) < 32:
        raise ValueError("ADMIN_TOKEN must contain at least 32 characters")
    role = app_role(environment)
    urls = []
    for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        value = environment.get(key, "")
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Both database URLs must use postgresql+psycopg")
        url = make_url(value)
        if not url.password or not url.host or url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("Both database URLs require credentials and explicit TLS")
        if url.port == 6543:
            raise ValueError("Use the session pooler or direct connection, not transaction mode")
        urls.append(url)
    application, migration = urls
    if application.username not in {role} and not (application.username or "").startswith(role + "."):
        raise ValueError("DATABASE_URL must use the dedicated restricted application role")
    if application.username == migration.username or (application.host, application.port, application.database) != (migration.host, migration.port, migration.database):
        raise ValueError("Use separate database roles targeting the same project endpoint")
    if (application.host or "").endswith(".pooler.supabase.com"):
        app_parts = (application.username or "").split(".", 1)
        migration_parts = (migration.username or "").split(".", 1)
        if (len(app_parts) != 2 or len(migration_parts) != 2
                or app_parts[1] != migration_parts[1] or app_parts[0] == migration_parts[0]):
            raise ValueError("Session-pooler URLs require distinct roles in the same project")
    if migration_search_path(environment.get("MIGRATION_SEARCH_PATH", "")) != "public, extensions":
        raise ValueError("Render requires MIGRATION_SEARCH_PATH=public,extensions")
    return parse_port(environment), root


def runtime_environment(environment: Mapping[str, str]) -> dict[str, str]:
    child = child_environment(environment)
    child.pop("APP_DB_PASSWORD", None)  # DATABASE_URL is already explicit.
    child.pop("MIGRATION_SEARCH_PATH", None)
    child.pop("SUPABASE_DB_PASSWORD", None)
    child.pop("BOOTSTRAP_FRESH_SUPABASE", None)
    return child


def prepare_cache(root: Path):
    if os.geteuid() != APP_UID or os.getegid() != APP_GID:
        raise ValueError("Render runtime must run as application UID/GID 10001")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not os.access(root, os.W_OK | os.X_OK):
        raise ValueError("Render's temporary audio cache is not writable")
    os.umask(0o027)


def prepare_database(environment: Mapping[str, str]) -> None:
    # Raw migration errors can contain connection/SQL details. Fail closed
    # with fixed diagnostics instead of forwarding them to public logs.
    result = subprocess.run([sys.executable, "-m", "scripts.setup_supabase", "--migrate"],
        env=dict(environment), capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError("Database preparation failed; check project access and migration permissions")


def verify_bucket(environment: Mapping[str, str]):
    result = subprocess.run([sys.executable, "-c",
        "from app.config import get_settings; from app.object_storage import check_audio_bucket; check_audio_bucket(get_settings())"],
        env=runtime_environment(environment), capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Private audio storage verification failed; check project credentials and bucket access")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        environment = resolved_environment(os.environ)
        port, root = validate_environment(environment)
        prepare_cache(root)
        LOGGER.info("Preparing database permissions and migrations")
        prepare_database(environment)
        LOGGER.info("Checking private audio bucket")
        verify_bucket(environment)
    except Exception as error:
        # No arbitrary exception strings or subprocess output reaches logs.
        LOGGER.error("Render startup checks failed (%s); verify database, private bucket and cloud-only settings.", type(error).__name__)
        return 1
    LOGGER.info("Migrations and private audio storage verified; starting application services")
    return run_services(service_commands(port), runtime_environment(environment))


if __name__ == "__main__":
    raise SystemExit(main())
