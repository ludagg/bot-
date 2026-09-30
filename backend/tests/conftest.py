import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5433/sih_test"
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    os.environ["DATABASE_URL"] = TEST_URL
    command.upgrade(Config("alembic.ini"), "head")
    yield eng
    eng.dispose()


@pytest.fixture
def clean(engine):
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        for t in ("publication_log", "detections", "entity_hourly", "entities", "raw_events", "sources"):
            conn.execute(text(f"TRUNCATE {t} CASCADE"))
    return engine
