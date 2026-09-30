import os

from alembic import context
from sqlalchemy import create_engine

from sih.config import get_settings

url = os.environ.get("DATABASE_URL") or get_settings().database_url


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
