"""Prepare an isolated Supabase database without exposing app tables via its API.

Bootstrap runs once against a fresh, dedicated project. Prepare/harden run at
each Render startup. Never run this against a project shared with another app.
Only fixed diagnostics are printed; SQL and credential values are not logged.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys

from sqlalchemy import create_engine, pool, text

from app.models import Base

LOGGER = logging.getLogger("noise.supabase_setup")
APP_TABLES = frozenset(Base.metadata.tables) | {"alembic_version"}
APP_SEQUENCES = frozenset({"measurements_result_order_seq"})
APP_FUNCTIONS = frozenset({("reject_immutable_row_change", "")})
API_ROLES = ("anon", "authenticated", "service_role")


def app_role(environment) -> str:
    role = environment.get("APP_DB_USER", "noise_app")
    if not re.fullmatch(r"(?:noise|urbanecho)_[a-z0-9_]{1,48}", role):
        raise ValueError("APP_DB_USER must be a dedicated noise_ or urbanecho_ role")
    return role


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def check_isolated_schema(connection, *, fresh=False):
    rows = connection.execute(text("""
        SELECT c.relname, c.relkind FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')
    """)).all()
    allowed = APP_TABLES | APP_SEQUENCES
    if (fresh and rows) or any(name not in allowed for name, _ in rows):
        raise ValueError("Refusing a database with existing unrelated public objects")
    functions = connection.execute(text("""
        SELECT p.proname, pg_get_function_identity_arguments(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
    """)).all()
    if (fresh and functions) or any(tuple(row) not in APP_FUNCTIONS for row in functions):
        raise ValueError("Refusing a database with existing public functions")


def check_role(connection, role: str):
    row = connection.execute(text("""
        SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit
        FROM pg_roles WHERE rolname=:role
    """), {"role": role}).one_or_none()
    if row is None or any(row):
        raise ValueError("The dedicated application role is missing or has elevated privileges")
    memberships = connection.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
                       WHERE r.rolname=:role)
    """), {"role": role})
    if memberships:
        raise ValueError("The dedicated application role must not inherit or assume other roles")


def secure_schema(connection, role: str):
    """Protect existing objects and defaults while preserving migration grants."""
    current = connection.scalar(text("SELECT current_user"))
    grantees = ["PUBLIC"]
    for name in API_ROLES:
        if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": name}):
            grantees.append(quote_identifier(name))
    denied = ", ".join(grantees)
    owner = quote_identifier(current)
    connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA public FROM {denied}")
    for objects in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        connection.exec_driver_sql(f"REVOKE ALL ON ALL {objects} IN SCHEMA public FROM {denied}")
        connection.exec_driver_sql(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public REVOKE ALL ON {objects} FROM {denied}")
    connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public, extensions TO {quote_identifier(role)}")
    if connection.scalar(text("""
        SELECT has_schema_privilege(:role, 'public', 'CREATE')
            OR has_schema_privilege(:role, 'extensions', 'CREATE')
            OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE r.rolname=:role)
            OR EXISTS (SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE r.rolname=:role)
            OR EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE r.rolname=:role)
    """), {"role": role}):
        raise ValueError("The application role must not own database objects or have schema creation rights")
    # This does not grant table writes: each forward migration grants only its
    # required operations, including SELECT/INSERT-only immutable history.
    for name in API_ROLES:
        if name in [item.strip('"') for item in grantees]:
            if connection.scalar(text("SELECT has_schema_privilege(:role, 'public', 'USAGE')"), {"role": name}):
                raise RuntimeError("A Data API role still has inherited public-schema access")


def prepare(connection, role: str, *, fresh=False, password=None):
    check_isolated_schema(connection, fresh=fresh)
    schema = connection.scalar(text("""
        SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
        WHERE e.extname='postgis'
    """))
    if schema is not None and schema != "extensions":
        raise ValueError("PostGIS must be installed in extensions in this dedicated project")
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS extensions")
    if fresh:
        if not password or len(password) < 32:
            raise ValueError("Bootstrap requires a fresh APP_DB_PASSWORD of at least 32 characters")
        if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role}):
            raise ValueError("Bootstrap refuses to replace an existing application role")
        # Send a SCRAM verifier, not the plaintext password, in role DDL.
        driver = connection.connection.driver_connection
        verifier = driver.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
        escaped = verifier.replace("'", "''")
        connection.exec_driver_sql(f"CREATE ROLE {quote_identifier(role)} LOGIN PASSWORD '{escaped}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS")
    check_role(connection, role)
    secure_schema(connection, role)
    if schema is None:
        connection.exec_driver_sql("CREATE EXTENSION postgis WITH SCHEMA extensions")
    database = connection.scalar(text("SELECT current_database()"))
    connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE {quote_identifier(database)} TO {quote_identifier(role)}")
    connection.exec_driver_sql(f"ALTER ROLE {quote_identifier(role)} IN DATABASE {quote_identifier(database)} SET search_path TO public, extensions")
    connection.exec_driver_sql(f"ALTER ROLE {quote_identifier(role)} IN DATABASE {quote_identifier(database)} SET timezone TO 'UTC'")
    connection.exec_driver_sql("SET LOCAL search_path TO public, extensions")
    connection.scalar(text("SELECT PostGIS_Full_Version()"))


def bootstrap_required(connection, role: str, environment) -> bool:
    """Only explicit first-deploy opt-in can create a missing dedicated role."""
    exists = connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role})
    if exists:
        return False  # Never replace its password or weaken its permissions.
    if environment.get("BOOTSTRAP_FRESH_SUPABASE", "").lower() != "true":
        raise ValueError("Missing dedicated role; explicit fresh-project bootstrap is required")
    check_isolated_schema(connection, fresh=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--bootstrap", action="store_true")
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--harden", action="store_true")
    modes.add_argument("--migrate", action="store_true")
    parser.add_argument("--confirm-fresh-project", action="store_true")
    args = parser.parse_args(argv)
    if args.bootstrap and not args.confirm_fresh_project:
        parser.error("--bootstrap requires --confirm-fresh-project for a dedicated empty project")
    engine = None
    try:
        role = app_role(os.environ)
        url = os.environ.get("MIGRATION_DATABASE_URL")
        if not url or not url.startswith("postgresql+psycopg://"):
            raise ValueError("MIGRATION_DATABASE_URL must use postgresql+psycopg")
        engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True,
                               connect_args={"connect_timeout": 10})
        with engine.connect() as connection:
            # Hold one session lock across prepare, Alembic and hardening so
            # overlapping deploys cannot race the same schema migration.
            # NullPool closes this session (and its lock) even on failure.
            connection.execute(text("SELECT pg_advisory_lock(741908732160)"))
            fresh = args.bootstrap or (args.migrate and bootstrap_required(connection, role, os.environ))
            prepare(connection, role, fresh=fresh, password=os.environ.get("APP_DB_PASSWORD"))
            connection.commit()
            if args.migrate:
                result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                    env=dict(os.environ), capture_output=True, timeout=240)
                if result.returncode:
                    raise RuntimeError("Schema migration failed")
                prepare(connection, role)
                connection.commit()
        print("Supabase application-role and schema access checks completed.")
        return 0
    except Exception as error:
        # Do not log SQLAlchemy exceptions, SQL, URLs, role passwords or keys.
        LOGGER.error("Supabase setup failed (%s); verify the dedicated project, extension schema and role permissions.", type(error).__name__)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
