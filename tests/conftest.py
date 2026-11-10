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
os.environ["MIN_WORKOUT_MINUTES"] = "30"
os.environ["MIN_WORKOUT_CALORIES"] = "400"
# Keep /log from scoring in unrelated tests; test_scoring.py sets its own start.
os.environ["SCORING_START_DATE"] = "2099-01-01"
os.environ["SCORING_ENABLED"] = "true"
os.environ["WEEKLY_WORKOUT_GOAL"] = "2"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from main import app, engine, issue_key  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE scoring_periods, ledger, rewards, daily_activity, api_keys, user_goals, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_key():
    """Create a person (if needed) and a fresh key for them. Returns the raw key."""

    def _make(name="brandan", label=None):
        with Session(engine) as db:
            return issue_key(db, name, label)

    return _make


@pytest.fixture
def headers(make_key):
    """Auth headers for a person. Creates their key once per test."""
    cache = {}

    def _headers(name="brandan"):
        if name not in cache:
            cache[name] = {"X-API-Key": make_key(name)}
        return cache[name]

    return _headers


@pytest.fixture
def auth(headers):
    return headers("brandan")


@pytest.fixture
def log(client, headers):
    """POST /log as a given person. The server decides who the user is from
    the key, so there is no name in the body."""

    def _log(as_user="brandan", **overrides):
        body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
        body.update(overrides)
        return client.post("/log", json=body, headers=headers(as_user))

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