import datetime as dt


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


def test_workout_day_needs_both_thresholds(client, auth, log):
    log(date="2026-11-01", minutes=30, calories=300)  # exactly on both thresholds
    log(date="2026-11-02", minutes=45, calories=200)  # calories too low
    log(date="2026-11-03", minutes=20, calories=400)  # minutes too low
    log(date="2026-11-04", minutes=45, calories=400)  # clearly a workout day

    data = client.get("/api/calendar?month=2026-11", headers=auth).json()
    days = data["days"]
    assert days["2026-11-01"]["brandan"]["workout"] is True
    assert days["2026-11-02"]["brandan"]["workout"] is False
    assert days["2026-11-03"]["brandan"]["workout"] is False
    assert days["2026-11-04"]["brandan"]["workout"] is True
    assert data["min_minutes"] == 30
    assert data["min_calories"] == 300


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


def test_calendar_separates_users(client, auth, log):
    log(name="brandan", date="2026-11-05", minutes=45, calories=400)
    log(name="sam", date="2026-11-05", minutes=10, calories=100)
    day = client.get("/api/calendar?month=2026-11", headers=auth).json()["days"]["2026-11-05"]
    assert day["brandan"]["workout"] is True
    assert day["sam"]["workout"] is False


def test_calendar_rejects_bad_month(client, auth):
    assert client.get("/api/calendar?month=2026-13", headers=auth).status_code == 422
    assert client.get("/api/calendar?month=nov", headers=auth).status_code == 422


def test_activity_respects_days_window(client, auth, log):
    today = dt.date.today()
    log(date=today.isoformat())
    log(date=(today - dt.timedelta(days=60)).isoformat())
    data = client.get("/api/activity?days=7", headers=auth).json()
    assert [r["date"] for r in data] == [today.isoformat()]
    assert data[0]["name"] == "brandan"