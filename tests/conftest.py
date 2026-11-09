import os

import pytest


def _norm(url):
    return (url or "").strip().strip("'\"").replace("+psycopg", "", 1)


TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.exit(
        "Set TEST_DATABASE_URL to a throwaway database (a Neon test branch or "
        "local Postgres). Tests TRUNCATE tables, so never point it at production.",
        returncode=2,
    )
if _norm(TEST_URL) == _norm(os.environ.get("DATABASE_URL")):
    pytest.exit("TEST_DATABASE_URL must differ from DATABASE_URL.", returncode=2)

# Must be set BEFORE main is imported (it reads these at import time).
os.environ["DATABASE_URL"] = TEST_URL
os.environ["API_KEY"] = "test-key"
os.environ["MIN_WORKOUT_MINUTES"] = "30"
os.environ["MIN_WORKOUT_CALORIES"] = "300"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from main import app, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE daily_activity, users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth():
    return {"X-API-Key": "test-key"}


@pytest.fixture
def log(client, auth):
    """POST /log with sensible defaults. When auth changes later (per-user
    keys), this is the one place to update."""

    def _log(**overrides):
        body = {"name": "Brandan", "date": "2026-11-08", "minutes": 45, "calories": 400}
        body.update(overrides)
        return client.post("/log", json=body, headers=auth)

    return _log


@pytest.fixture
def rows():
    """Read daily_activity straight from the database."""

    def _rows():
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT u.name, d.date::text AS date, "
                    "d.exercise_minutes, d.active_calories "
                    "FROM daily_activity d JOIN users u ON u.id = d.user_id "
                    "ORDER BY d.date, u.name"
                )
            ).mappings()
            return [dict(r) for r in result]

    return _rows