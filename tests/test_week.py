"""The "This week" panel: GET /api/week and the pacing rules behind it."""
import datetime as dt

import pytest
from sqlalchemy.orm import Session

from app import config
from app.db import engine
from app.models import ScoringPeriod
from app.week_progress import pace

# Week of Mon Nov 9 .. Sun Nov 15, 2026; today is Thursday.
TODAY = dt.date(2026, 11, 12)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(config, "today", lambda: TODAY)


def week(client, auth):
    r = client.get("/api/week", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "done, target, left, elapsed, span, expected",
    [
        (2, 2, 4, 3, 7, "done"),
        (0, 5, 4, 3, 7, "out_of_reach"),  # 5 needed, only 4 days left
        (4, 5, 3, 4, 7, "almost"),
        (2, 5, 3, 4, 7, "no_slack"),      # 3 needed in 3 days
        (3, 5, 4, 3, 7, "on_pace"),       # 3/3 days vs 5/7 pace
        (1, 5, 5, 2, 7, "behind"),        # 1 hit in 2 days, pace is ~1.4
        (0, 2, 7, 0, 7, "on_pace"),       # Monday morning, nothing over yet
    ],
)
def test_pace(done, target, left, elapsed, span, expected):
    assert pace(done, target, left, elapsed, span) == expected


def test_needs_a_key(client):
    assert client.get("/api/week").status_code == 401


def test_no_goals_means_no_progress(client, auth):
    w = week(client, auth)
    assert w["me"] is None
    assert (w["monday"], w["sunday"], w["days_left"]) == ("2026-11-09", "2026-11-15", 4)


def test_nothing_about_teammates_is_returned(client, auth, headers, log, set_goals):
    set_goals("brandan", "ana")
    log(as_user="ana", date="2026-11-10", minutes=45, calories=900)
    w = week(client, auth)
    assert set(w) == {"monday", "sunday", "today", "days_left", "me", "streak"}
    assert "ana" not in str(w)
    # Each person gets their own numbers.
    assert week(client, headers("ana"))["me"]["calories"]["done"] == 1
    assert w["me"]["calories"]["done"] == 0


def test_progress_so_far(client, auth, log, set_goals):
    set_goals("brandan")  # 30 min, 400 cal, 2 workouts a week
    log(date="2026-11-09", minutes=45, calories=400)
    log(date="2026-11-10", minutes=45, calories=900)
    log(date="2026-11-11", minutes=10, calories=400)   # calories only
    log(date="2026-11-08", minutes=45, calories=900)   # last week: ignored
    p = week(client, auth)["me"]
    assert p["workouts"] == {"done": 2, "target": 2, "left": 4, "status": "done"}
    assert p["calories"]["done"] == 3 and p["calories"]["target"] == 5
    assert p["calories"]["left"] == 4  # today (not hit yet) through Sunday
    assert p["calories"]["status"] == "on_pace"
    assert p["overall"] == "on_track"
    assert p["grace"] is False


def test_today_counts_once_hit(client, auth, log, set_goals):
    set_goals("brandan")
    log(date="2026-11-12", minutes=45, calories=400)
    p = week(client, auth)["me"]
    assert p["calories"]["done"] == 1
    assert p["calories"]["left"] == 3  # today is already in `done`
    assert p["workouts"]["done"] == 1


def test_future_days_do_not_count(client, auth, log, set_goals):
    set_goals("brandan")
    for d in range(9, 13):
        log(date=f"2026-11-{d:02d}", minutes=45, calories=400)
    log(date="2026-11-13", minutes=0, calories=400)  # synced ahead (tomorrow's date)
    p = week(client, auth)["me"]
    # Friday is after today, so it does not count yet.
    assert p["calories"]["done"] == 4
    assert p["calories"]["status"] == "almost"


def test_out_of_reach(client, auth, log, set_goals):
    set_goals("brandan")
    for d in range(9, 12):
        log(date=f"2026-11-{d:02d}", minutes=45, calories=100)  # under goal
    p = week(client, auth)["me"]
    assert p["calories"]["status"] == "out_of_reach"  # 5 needed, 4 days left
    assert p["overall"] == "out"


def test_grace_week(client, auth, set_goals):
    # Goals set today (Thursday): only Thursday on is judged.
    client.patch(
        "/api/me/settings",
        json={"min_minutes": 30, "min_calories": 400, "weekly_workouts": 2},
        headers=auth,
    )
    p = week(client, auth)["me"]
    assert p["grace"] is True
    assert p["calories"]["left"] == 4


def test_streak(client, auth, set_goals, monkeypatch):
    set_goals("brandan")
    with Session(engine) as db:
        db.add(ScoringPeriod(period="wk-2026-10-26", kind="week", pair_ok=True, streak=2, multiplier="1.1"))
        db.add(ScoringPeriod(period="wk-2026-11-02", kind="week", pair_ok=True, streak=3, multiplier="1.1"))
        db.commit()
    s = week(client, auth)["streak"]
    assert s == {"weeks": 3, "multiplier": "1.1", "last_week_settled": True, "next_multiplier": "1.25"}

    monkeypatch.setattr(config, "SCORING_ENABLED", False)
    assert week(client, auth)["streak"] is None


def test_streak_not_promised_before_last_week_is_scored(client, auth, set_goals):
    set_goals("brandan")
    with Session(engine) as db:
        db.add(ScoringPeriod(period="wk-2026-10-26", kind="week", pair_ok=True, streak=2, multiplier="1.1"))
        db.commit()
    s = week(client, auth)["streak"]
    assert s["weeks"] == 2 and s["last_week_settled"] is False and s["next_multiplier"] is None
