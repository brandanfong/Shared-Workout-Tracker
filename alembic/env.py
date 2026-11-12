"""Runs migrations against DATABASE_URL using the app's own models.

When run from the command line (`alembic ...`) with DATABASE_URL not set, it is
read from .env, so the CLI points at the REAL database by default.
"""
import os

from alembic import context

from app.envfile import read_env

if "DATABASE_URL" not in os.environ:
    os.environ.update(read_env())

from app import models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.db import Base, engine  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse a connection handed over by app/setup_db.py, else open our own.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
