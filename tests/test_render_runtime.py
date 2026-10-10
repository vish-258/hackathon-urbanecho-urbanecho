"""Cloud launcher tests never connect to external services or read .env."""
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from scripts import serve_render as runtime
from scripts import setup_supabase as setup
from scripts.migration_config import migration_search_path


def environment():
    return {
        "LOCAL_BROWSER_ACCESS": "false", "AUDIO_STORAGE_BACKEND": "supabase",
        "AUDIO_ROOT": str(runtime.CACHE_ROOT), "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "synthetic-key", "SUPABASE_STORAGE_BUCKET": "originals",
        "ADMIN_TOKEN": "synthetic-" + "x" * 40, "APP_DB_USER": "noise_app",
        "DATABASE_URL": "postgresql+psycopg://noise_app.project:app-secret@region.pooler.supabase.com:5432/postgres?sslmode=require",
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://postgres.project:migration-secret@region.pooler.supabase.com:5432/postgres?sslmode=require",
        "MIGRATION_SEARCH_PATH": "public,extensions", "PORT": "19007",
    }


def test_valid_cloud_configuration():
    assert runtime.validate_environment(environment()) == (19007, runtime.CACHE_ROOT)


@pytest.mark.parametrize("key,value", [
    ("LOCAL_BROWSER_ACCESS", "true"), ("LOCAL_BROWSER_ACCESS", ""),
    ("AUDIO_STORAGE_BACKEND", "filesystem"), ("AUDIO_ROOT", "/data/audio"),
    ("SUPABASE_URL", "http://project.supabase.co"), ("SUPABASE_SERVICE_ROLE_KEY", ""),
    ("ADMIN_TOKEN", "short"), ("MIGRATION_SEARCH_PATH", "public"),
    ("DATABASE_URL", "postgresql+psycopg://postgres.project:secret@region.pooler.supabase.com:5432/postgres?sslmode=require"),
    ("DATABASE_URL", "postgresql+psycopg://noise_app.project:secret@region.pooler.supabase.com:6543/postgres?sslmode=require"),
    ("DATABASE_URL", "postgresql+psycopg://noise_app.project:secret@region.pooler.supabase.com:5432/postgres"),
    ("MIGRATION_DATABASE_URL", "postgresql+psycopg://postgres.project:secret@other.pooler.supabase.com:5432/postgres?sslmode=require"),
    ("MIGRATION_DATABASE_URL", "postgresql+psycopg://postgres.otherproject:secret@region.pooler.supabase.com:5432/postgres?sslmode=require"),
])
def test_unsafe_cloud_configuration_rejected(key, value):
    values = environment()
    values[key] = value
    with pytest.raises(ValueError):
        runtime.validate_environment(values)


def test_separate_password_fields_escape_reserved_characters():
    values = environment()
    del values["DATABASE_URL"], values["MIGRATION_DATABASE_URL"]
    password = "secret@:/?#%" + "x" * 32
    values.update(SUPABASE_POOLER_HOST="aws-0-ap-southeast-1.pooler.supabase.com",
        SUPABASE_PROJECT_REF="abcdefgh", SUPABASE_DB_PASSWORD=password, APP_DB_PASSWORD=password + "a")
    resolved = runtime.resolved_environment(values)
    assert make_url(resolved["MIGRATION_DATABASE_URL"]).password == password
    assert make_url(resolved["DATABASE_URL"]).password == password + "a"
    assert make_url(resolved["DATABASE_URL"]).username == "noise_app.abcdefgh"
    assert runtime.validate_environment(resolved)[0] == 19007
    assert "DATABASE_URL" not in values  # Caller configuration is not mutated.


def test_privileged_secrets_never_reach_runtime_or_storage_check(monkeypatch):
    values = environment() | {"APP_DB_PASSWORD": "secret", "POSTGRES_PASSWORD": "secret",
        "POSTGRES_USER": "postgres", "SUPABASE_DB_PASSWORD": "secret", "BOOTSTRAP_FRESH_SUPABASE": "true"}
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(returncode=0))
    runtime.verify_bucket(values)
    child = calls[0][1]["env"]
    for key in ("MIGRATION_DATABASE_URL", "APP_DB_PASSWORD", "POSTGRES_PASSWORD", "POSTGRES_USER",
                "SUPABASE_DB_PASSWORD", "MIGRATION_SEARCH_PATH", "BOOTSTRAP_FRESH_SUPABASE"):
        assert key not in child
    assert child["DATABASE_URL"] == values["DATABASE_URL"]
    assert child["SUPABASE_SERVICE_ROLE_KEY"] == values["SUPABASE_SERVICE_ROLE_KEY"]


def test_migration_failure_stops_before_bucket_or_process_start(monkeypatch, caplog):
    monkeypatch.setattr(runtime.os, "environ", environment())
    monkeypatch.setattr(runtime, "prepare_cache", lambda root: None)
    def failed(_):
        raise RuntimeError("secret-must-not-appear")
    monkeypatch.setattr(runtime, "prepare_database", failed)
    monkeypatch.setattr(runtime, "verify_bucket", lambda _: pytest.fail("bucket ran after failed migration"))
    monkeypatch.setattr(runtime, "run_services", lambda *_: pytest.fail("services ran after failed migration"))
    assert runtime.main() == 1
    assert "secret-must-not-appear" not in caplog.text


def test_all_startup_checks_precede_three_services(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.os, "environ", environment())
    for name in ("prepare_cache", "prepare_database", "verify_bucket"):
        monkeypatch.setattr(runtime, name, lambda _, name=name: calls.append(name))
    def run(commands, child):
        assert calls == ["prepare_cache", "prepare_database", "verify_bucket"]
        assert [name for name, _ in commands] == ["api", "worker", "classifier"]
        assert "MIGRATION_DATABASE_URL" not in child
        return 0
    monkeypatch.setattr(runtime, "run_services", run)
    assert runtime.main() == 0


def test_raw_migration_output_is_not_exposed(monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr=b"private-database-password"))
    with pytest.raises(RuntimeError, match="Database preparation failed") as error:
        runtime.prepare_database(environment())
    assert "private-database-password" not in str(error.value)


@pytest.mark.parametrize("path", ["extensions", "public,evil", 'public; DROP SCHEMA public', '"public"', ""])
def test_search_path_rejects_arbitrary_sql(path):
    with pytest.raises(ValueError):
        migration_search_path(path)


def test_supported_search_paths():
    assert migration_search_path() == "public"
    assert migration_search_path("public, extensions") == "public, extensions"


@pytest.mark.parametrize("role", ["postgres", "service_role", "anon", "noise_app;drop", 'noise_"app'])
def test_bootstrap_role_is_dedicated(role):
    with pytest.raises(ValueError):
        setup.app_role({"APP_DB_USER": role})


class SchemaConnection:
    def __init__(self, *, role_exists=False, objects=(), functions=False):
        self.role_exists, self.objects, self.functions = role_exists, objects, functions
    def scalar(self, sql, values=None):
        return self.role_exists if "pg_roles" in str(sql) else self.functions
    def execute(self, sql):
        rows = ([("unrelated", "")] if self.functions else []) if "pg_proc" in str(sql) else self.objects
        return SimpleNamespace(all=lambda: rows)


def test_bootstrap_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="explicit"):
        setup.bootstrap_required(SchemaConnection(), "noise_app", {})


def test_bootstrap_never_resets_existing_role():
    assert not setup.bootstrap_required(SchemaConnection(role_exists=True), "noise_app", {"BOOTSTRAP_FRESH_SUPABASE": "true"})


@pytest.mark.parametrize("objects,functions", [([("unrelated", "r")], False), ([("locations", "r")], False), ([], True)])
def test_bootstrap_refuses_any_existing_public_objects(objects, functions):
    with pytest.raises(ValueError, match="Refusing"):
        setup.bootstrap_required(SchemaConnection(objects=objects, functions=functions), "noise_app", {"BOOTSTRAP_FRESH_SUPABASE": "true"})


def test_bootstrap_accepts_only_missing_role_and_empty_schema():
    assert setup.bootstrap_required(SchemaConnection(), "noise_app", {"BOOTSTRAP_FRESH_SUPABASE": "true"})
