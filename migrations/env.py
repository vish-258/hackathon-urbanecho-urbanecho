"""Migrations use a separate privileged URL; app credentials never perform DDL."""
import os
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, InterfaceError

from app.models import Base
from scripts.migration_config import migration_search_path

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def include_object(obj, name, object_type, reflected, compare_to):
    # PostGIS owns this table. It is intentionally absent from our ORM models;
    # autogenerate must never suggest removing an extension's reference data.
    return not (object_type == "table" and name == "spatial_ref_sys")


def migration_url():
    explicit = os.environ.get("MIGRATION_DATABASE_URL")
    if explicit:
        return explicit
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("Migrations require MIGRATION_DATABASE_URL or POSTGRES_PASSWORD")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("POSTGRES_USER", "noise_migrate"),
        password=password,
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "noise_monitor"),
    )


def run_migrations_offline():
    context.configure(url=migration_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"},
                      include_object=include_object)
    with context.begin_transaction():
        context.execute("SET search_path TO " + migration_search_path(os.environ.get("MIGRATION_SEARCH_PATH", "public")))
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(migration_url(), poolclass=pool.NullPool,
                           connect_args={"connect_timeout": 5})
    attempts = int(os.environ.get("DB_RETRY_ATTEMPTS", "10"))
    for attempt in range(attempts):
        try:
            connection = engine.connect()
            break
        except (OperationalError, InterfaceError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(2 ** attempt, 10))
    with connection:
        # Local PostGIS remains in public; Supabase installs it in extensions.
        # Never inherit tiger/topology or an arbitrary externally supplied path.
        path = migration_search_path(os.environ.get("MIGRATION_SEARCH_PATH", "public"))
        connection.exec_driver_sql("SET SESSION search_path TO " + path)
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
