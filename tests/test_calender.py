import datetime as dt
from app import config


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_dashboard_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Workout Tracker" in r.text


def test_calendar_requires_key(client):
    assert client.get("/api/calendar?month=2026-11").status_code == 401


def test_activity_requires_key(client):
    assert client.get("/api/activity").status_code == 401


def test_workout_day_needs_both_thresholds(client, auth, log, set_goals):
    set_goals("brandan")
    log(date="2026-11-01", minutes=30, calories=400)  # exactly on both thresholds
    log(date="2026-11-02", minutes=45, calories=300)  # calories too low
    log(date="2026-11-03", minutes=20, calories=500)  # minutes too low
    log(date="2026-11-04", minutes=45, calories=500)  # clearly a workout day

    data = client.get("/api/calendar?month=2026-11", headers=auth).json()
    days = data["days"]
    assert days["2026-11-01"]["brandan"]["workout"] is True
    assert days["2026-11-02"]["brandan"]["workout"] is False
    assert days["2026-11-03"]["brandan"]["workout"] is False
    assert days["2026-11-04"]["brandan"]["workout"] is True
    assert data["goals"]["brandan"] == {"min_minutes": 30, "min_calories": 400, "weekly_workouts": 2}


def test_calendar_only_includes_requested_month(client, auth, log):
    log(date="2026-10-31")
    log(date="2026-11-01")
    log(date="2026-11-30")
    log(date="2026-12-01")
    days = client.get("/api/calendar?month=2026-11", headers=auth).json()["days"]
    assert sorted(days) == ["2026-11-01", "2026-11-30"]


def test_december_does_not_leak_into_january(client, auth, log):
    log(date="2026-12-31")
    log(date="2027-01-01")
    days = client.get("/api/calendar?month=2026-12", headers=auth).json()["days"]
    assert sorted(days) == ["2026-12-31"]


def test_calendar_lists_users_even_without_rows_that_month(client, auth, log):
    log(date="2026-10-15")
    data = client.get("/api/calendar?month=2026-11", headers=auth).json()
    assert data["users"] == ["brandan"]
    assert data["days"] == {}


def test_calendar_separates_users(client, auth, log, set_goals):
    set_goals("brandan", "sam")
    log(as_user="brandan", date="2026-11-05", minutes=45, calories=400)
    log(as_user="sam", date="2026-11-05", minutes=10, calories=100)
    day = client.get("/api/calendar?month=2026-11", headers=auth).json()["days"]["2026-11-05"]
    assert day["brandan"]["workout"] is True
    assert day["sam"]["workout"] is False


def test_calendar_rejects_bad_month(client, auth):
    assert client.get("/api/calendar?month=2026-13", headers=auth).status_code == 422
    assert client.get("/api/calendar?month=nov", headers=auth).status_code == 422


def test_activity_respects_days_window(client, auth, log):
    today = config.today()
    log(date=today.isoformat())
    log(date=(today - dt.timedelta(days=60)).isoformat())
    data = client.get("/api/activity?days=7", headers=auth).json()
    assert [r["date"] for r in data] == [today.isoformat()]
    assert data[0]["name"] == "brandan"

def test_calendar_marks_partial_days(client, headers, log, set_goals):
    set_goals("brandan")
    # defaults: 30 min and 400 cal. A workout day needs both.
    log(as_user="brandan", date="2026-11-02", minutes=45, calories=500)   # both
    log(as_user="brandan", date="2026-11-03", minutes=10, calories=500)   # calories only
    log(as_user="brandan", date="2026-11-04", minutes=45, calories=100)   # minutes only
    log(as_user="brandan", date="2026-11-05", minutes=10, calories=100)   # neither
    days = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()["days"]
    flags = {d: (v["brandan"]["workout"], v["brandan"]["calorie_hit"], v["brandan"]["minutes_hit"])
             for d, v in days.items()}
    assert flags == {
        "2026-11-02": (True, True, True),
        "2026-11-03": (False, True, False),
        "2026-11-04": (False, False, True),
        "2026-11-05": (False, False, False),
    }
