"""Opt-in bootstrap verification against a disposable, initially empty database.

Use SUPABASE_SETUP_TEST_URL only for a throwaway PostGIS-capable cluster.
This test creates cluster roles as well as application tables.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from scripts import setup_supabase as setup


def test_fresh_bootstrap_migrations_and_data_api_isolation(monkeypatch):
    url = os.environ.get("SUPABASE_SETUP_TEST_URL")
    if not url:
        pytest.skip("Requires a separate, empty disposable Supabase setup test cluster")
    parsed = make_url(url)
    assert parsed.database and parsed.database.endswith("_setup_test")
    engine = create_engine(url)
    password = "synthetic-setup-test-only-" + "x" * 32
    with engine.begin() as connection:
        setup.check_isolated_schema(connection, fresh=True)
        # Reproduce Supabase's Data API roles and historical public defaults.
        for role in setup.API_ROLES:
            assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role})
            connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            connection.exec_driver_sql(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON {kind} TO anon, authenticated, service_role")

    monkeypatch.setenv("MIGRATION_DATABASE_URL", url)
    monkeypatch.setenv("MIGRATION_SEARCH_PATH", "public,extensions")
    monkeypatch.setenv("APP_DB_USER", "noise_app")
    monkeypatch.setenv("APP_DB_PASSWORD", password)
    monkeypatch.setenv("BOOTSTRAP_FRESH_SUPABASE", "true")
    assert setup.main(["--migrate"]) == 0
    monkeypatch.setenv("APP_DB_PASSWORD", "ignored-new-password-" + "y" * 32)
    assert setup.main(["--migrate"]) == 0  # Restart is idempotent.

    with engine.begin() as connection:
        setup.check_role(connection, "noise_app")
        setup.check_isolated_schema(connection)
        assert connection.scalar(text("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE extname='postgis'")) == "extensions"
        for role in setup.API_ROLES:
            assert not connection.scalar(text("SELECT has_schema_privilege(:role,'public','USAGE')"), {"role": role})
            for table in setup.APP_TABLES:
                assert not connection.scalar(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"), {"role": role, "table": f"public.{table}"})
            assert not connection.scalar(text("SELECT has_sequence_privilege(:role,'public.measurements_result_order_seq','USAGE,SELECT,UPDATE')"), {"role": role})
            assert not connection.scalar(text("SELECT has_function_privilege(:role,'public.reject_immutable_row_change()','EXECUTE')"), {"role": role})
        # Future tables inherit the hardened defaults even before a later
        # migration explicitly grants the restricted app its required rights.
        connection.exec_driver_sql("CREATE TABLE public.future_private_probe (id integer)")
        for role in setup.API_ROLES:
            assert not connection.scalar(text("SELECT has_table_privilege(:role,'public.future_private_probe','SELECT')"), {"role": role})
        connection.exec_driver_sql("DROP TABLE public.future_private_probe")

    with engine.connect() as connection:
        connection.exec_driver_sql("GRANT CREATE ON SCHEMA public TO noise_app")
        with pytest.raises(ValueError, match="schema creation"):
            setup.prepare(connection, "noise_app")
        connection.rollback()
        connection.exec_driver_sql("ALTER ROLE noise_app INHERIT")
        with pytest.raises(ValueError, match="elevated"):
            setup.check_role(connection, "noise_app")
        connection.rollback()

    application = create_engine(parsed.set(username="noise_app", password=password))
    with application.connect() as connection:
        assert connection.scalar(text("SHOW search_path")) == "public, extensions"
        assert connection.scalar(text("SELECT ST_AsText(ST_Point(1,2))")) == "POINT(1 2)"
        assert connection.scalar(text("SELECT ST_Distance(ST_SetSRID(ST_Point(1,2),4326)::geography,ST_SetSRID(ST_Point(1,2),4326)::geography)")) == 0
        for table in setup.APP_TABLES:
            assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) >= 0
        assert connection.scalar(text("SELECT has_table_privilege(current_user,'public.durable_events','INSERT')"))
        for table in ("durable_events", "measurement_evaluations", "threshold_versions"):
            assert not connection.scalar(text("SELECT has_table_privilege(current_user,:table,'UPDATE,DELETE')"), {"table": f"public.{table}"})
        assert not connection.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
        with pytest.raises(DBAPIError):
            connection.exec_driver_sql("CREATE TABLE public.forbidden (id integer)")
        connection.rollback()
    application.dispose()
    engine.dispose()
