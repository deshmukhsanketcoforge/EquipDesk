"""Alembic environment: online migrations against DATABASE_URL (or a caller-set URL)."""

from alembic import context
from sqlalchemy import create_engine, pool

from server.config import Settings
from server.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not supported.")
run_migrations()
