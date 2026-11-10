import datetime as dt

import pytest

import main

TODAY = dt.date(2026, 11, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(main, "_today", lambda: TODAY)


@pytest.fixture
def seeded(log):
    for day in range(1, 11):  # Nov 1 .. Nov 10
        log(as_user="brandan", date=f"2026-11-{day:02d}", minutes=40, calories=500)


def dates(client, auth, query):
    r = client.get(f"/api/activity?{query}", headers=auth)
    assert r.status_code == 200, r.text
    return [row["date"][-2:] for row in r.json()]


def test_days_mode_is_unchanged(client, auth, seeded):
    assert dates(client, auth, "days=7") == ["03", "04", "05", "06", "07", "08", "09", "10"]
    assert dates(client, auth, "") [0] == "01"  # default 30 days covers all of it


def test_custom_range_is_inclusive(client, auth, seeded):
    assert dates(client, auth, "start=2026-11-02&end=2026-11-04") == ["02", "03", "04"]


def test_start_only_runs_to_today(client, auth, seeded):
    assert dates(client, auth, "start=2026-11-08") == ["08", "09", "10"]


def test_end_only_looks_back_from_it(client, auth, seeded):
    assert dates(client, auth, "end=2026-11-04&days=3") == ["01", "02", "03", "04"]


def test_one_day_range(client, auth, seeded):
    assert dates(client, auth, "start=2026-11-05&end=2026-11-05") == ["05"]


def test_range_with_no_data_is_empty_not_an_error(client, auth, seeded):
    assert dates(client, auth, "start=2025-01-01&end=2025-01-31") == []


@pytest.mark.parametrize(
    "query",
    [
        "start=2026-11-05&end=2026-11-01",       # backwards
        "start=2020-01-01&end=2026-11-10",       # longer than two years
        "start=not-a-date",
        "start=2026-13-45&end=2026-13-46",
        "days=0",
        "days=500",
    ],
)
def test_bad_ranges_are_rejected(client, auth, query):
    assert client.get(f"/api/activity?{query}", headers=auth).status_code == 422


def test_activity_requires_a_key(client):
    assert client.get("/api/activity?days=7").status_code == 401
