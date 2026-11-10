import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import main
import scoring
from main import DailyActivity, User, engine, run_scoring
from scoring import Goals
from sqlalchemy import select

D = dt.date
MON = D(2026, 11, 2)  # first full week after the Nov 1 start


# ---------- pure rules (the worked example from the scoring doc) ----------


def _week(per_day, monday=MON):
    return {monday + dt.timedelta(days=i): v for i, v in enumerate(per_day)}


# (minutes, calories) shorthand for Brandan, calorie goal 500
B_WORKOUT = (45, 750)  # 150% of goal and a workout day
B_HIT = (0, 500)  # exactly 100%
BRANDAN = Goals(min_minutes=30, min_calories=500, weekly_workouts=4)
PARTNER = Goals(min_minutes=30, min_calories=400, weekly_workouts=2)


def test_doc_example_brandan_week_1():
    w = scoring.score_player_week(_week([B_WORKOUT] * 4 + [B_HIT] * 3), MON, BRANDAN)
    assert (w.calorie_daily, w.calorie_weekly, w.workout_day, w.workout_goal) == (11, 5, 8, 4)
    assert w.subtotal == 28 and w.ok


def test_doc_example_partner_week_1():
    w = scoring.score_player_week(_week([(45, 600)] * 2 + [(0, 400)] * 5), MON, PARTNER)
    assert w.subtotal == 22 and w.ok


def test_doc_example_multiplier_rounding():
    m = scoring.multiplier_for(2)
    assert m == Decimal("1.1")
    assert (scoring.scaled(28, m), scoring.scaled(22, m), scoring.scaled(4, m)) == (31, 24, 4)


def test_missed_workout_goal_blocks_but_keeps_direct_points():
    w = scoring.score_player_week(_week([B_WORKOUT] * 3 + [B_HIT] * 4), MON, BRANDAN)
    assert w.workout_days == 3 and w.workout_goal == 0 and not w.ok
    # 3 x 150% (6) + 4 x 100% (4) + 7/7 bonus (5) + 3 workouts (6), no goal bonus
    assert w.subtotal == 21


@pytest.mark.parametrize(
    "calories,pts",
    [(0, 0), (499, 0), (500, 1), (749, 1), (750, 2), (999, 2), (1000, 3), (5000, 3)],
)
def test_calorie_tiers_pay_highest_only(calories, pts):
    assert scoring.calorie_day_points(calories, 500) == pts


@pytest.mark.parametrize("streak,mult", [(0, "1.0"), (1, "1.0"), (2, "1.1"), (3, "1.1"),
                                         (4, "1.25"), (7, "1.25"), (8, "1.5"), (30, "1.5")])
def test_multiplier_table(streak, mult):
    assert scoring.multiplier_for(streak) == Decimal(mult)


def test_weekly_consistency_bonus():
    goals = Goals(30, 400, 99)
    six = scoring.score_player_week(_week([(0, 400)] * 6), MON, goals)
    seven = scoring.score_player_week(_week([(0, 400)] * 7), MON, goals)
    five = scoring.score_player_week(_week([(0, 400)] * 5), MON, goals)
    assert (five.calorie_weekly, six.calorie_weekly, seven.calorie_weekly) == (0, 3, 5)


def test_workout_day_needs_both_thresholds():
    assert not scoring.is_workout_day(45, 399, PARTNER)
    assert not scoring.is_workout_day(29, 900, PARTNER)
    assert scoring.is_workout_day(30, 400, PARTNER)


@pytest.mark.parametrize("hit,days,pts", [(30, 30, 20), (27, 30, 10), (24, 30, 5), (23, 30, 0),
                                          (28, 31, 10), (25, 31, 5)])
def test_monthly_tiers(hit, days, pts):
    assert scoring.monthly_points(hit, days) == pts


# ---------- the runner against the database ----------


@pytest.fixture(autouse=True)
def start_nov_1(monkeypatch):
    monkeypatch.setattr(main, "SCORING_START", D(2026, 11, 1))


@pytest.fixture
def players(client, headers):
    """brandan: 500 cal, 4 workouts a week. partner: 400 cal, 2 a week."""
    client.patch("/api/me/settings", headers=headers("brandan"),
                 json={"min_calories": 500, "weekly_workouts": 4})
    client.patch("/api/me/settings", headers=headers("partner"),
                 json={"min_calories": 400, "weekly_workouts": 2})


@pytest.fixture
def put():
    """Insert daily rows directly: put('brandan', MON, [(min, cal), ...])."""

    def _put(name, monday, per_day):
        with Session(engine) as db:
            uid = db.scalar(select(User.id).where(User.name == name))
            for i, (mins, cal) in enumerate(per_day):
                db.add(DailyActivity(user_id=uid, date=monday + dt.timedelta(days=i),
                                     exercise_minutes=mins, active_calories=cal))
            db.commit()

    return _put


def good_week(put, monday=MON, brandan_workouts=4):
    put("brandan", monday, [B_WORKOUT] * brandan_workouts + [B_HIT] * (7 - brandan_workouts))
    put("partner", monday, [(45, 600)] * 2 + [(0, 400)] * 5)


def score(today):
    with Session(engine) as db:
        return run_scoring(db, today)


def balance(client, auth):
    return client.get("/api/balance", headers=auth).json()


def test_week_scores_on_tuesday_not_before(client, auth, players, put):
    good_week(put)
    assert score(D(2026, 11, 9)) == []  # Monday after: still in the grace day
    assert score(D(2026, 11, 10)) == ["wk-2026-11-02"]
    b = balance(client, auth)
    # 28 + 22 + 4 team bonus (split 2 and 2)
    assert b["balance"] == 54 and b["earned_by"] == {"brandan": 30, "partner": 24}


def test_scoring_is_idempotent(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 10))
    assert score(D(2026, 11, 10)) == []
    assert score(D(2026, 11, 12)) == []
    assert balance(client, auth)["balance"] == 54


def test_late_data_does_not_change_a_scored_week(client, auth, players, put):
    put("brandan", MON, [B_HIT] * 7)
    put("partner", MON, [(0, 400)] * 7)
    score(D(2026, 11, 10))
    before = balance(client, auth)["balance"]
    with engine.begin() as conn:  # a backfill upgrades every day afterwards
        conn.execute(main.text("UPDATE daily_activity SET active_calories = 5000, exercise_minutes = 90"))
    score(D(2026, 11, 20))
    assert balance(client, auth)["balance"] == before


def test_waits_for_partner_sync_then_times_out(client, auth, players, put):
    put("brandan", MON, [B_WORKOUT] * 4 + [B_HIT] * 3)
    put("partner", MON, [(45, 600)] * 2 + [(0, 400)] * 4)  # no Sunday row
    assert score(D(2026, 11, 10)) == []  # partner has not synced Sunday yet
    assert score(D(2026, 11, 14)) == []
    assert score(D(2026, 11, 15)) == ["wk-2026-11-02"]  # 7 days later: score anyway
    assert balance(client, auth)["earned_by"]["brandan"] > 0


def test_streak_multiplier_applies_in_week_two(client, auth, players, put):
    good_week(put, MON)
    good_week(put, MON + dt.timedelta(days=7))
    assert score(D(2026, 11, 17)) == ["wk-2026-11-02", "wk-2026-11-09"]
    b = balance(client, auth)
    # week 1 = 54; week 2 at 1.1x: 31 + 24 + 4 = 59
    assert b["balance"] == 54 + 59
    cats = {e["category"] for e in client.get("/api/ledger?type=earn", headers=auth).json()}
    assert "multiplier" in cats


def test_blocked_week_resets_streak_but_pays_direct_points(client, auth, players, put):
    good_week(put, MON)
    good_week(put, MON + dt.timedelta(days=7), brandan_workouts=3)  # misses goal of 4
    good_week(put, MON + dt.timedelta(days=14))
    score(D(2026, 11, 24))
    with Session(engine) as db:
        rows = {p.period: (p.pair_ok, p.streak) for p in db.scalars(select(main.ScoringPeriod))
                if p.kind == "week"}
    assert rows == {"wk-2026-11-02": (True, 1), "wk-2026-11-09": (False, 0),
                    "wk-2026-11-16": (True, 1)}
    # week 2 paid direct points but no team bonus
    wk2 = [e for e in client.get("/api/ledger?limit=1000", headers=auth).json()
           if e["period"] == "wk-2026-11-09"]
    assert wk2 and not any(e["category"] == "team_bonus" for e in wk2)


def test_big_backfill_replays_weeks_in_order(client, auth, players, put):
    for i in range(3):
        good_week(put, MON + dt.timedelta(days=7 * i))
    assert score(D(2026, 12, 1)) == ["wk-2026-11-02", "wk-2026-11-09", "wk-2026-11-16"]


def test_days_before_start_are_ignored(client, auth, players, put):
    good_week(put, D(2026, 10, 26))  # the week before the start
    assert score(D(2026, 11, 10)) == []
    assert balance(client, auth)["balance"] == 0


def test_monthly_bonus(client, auth, players, put):
    for name in ("brandan", "partner"):
        put(name, D(2026, 11, 1), [(0, 1000)] * 30)  # every day of November
    scored = score(D(2026, 12, 2))
    assert "2026-11" in scored
    monthly = [e for e in client.get("/api/ledger?limit=1000", headers=auth).json()
               if e["category"] == "calorie_monthly"]
    assert sorted((e["player"], e["points"]) for e in monthly) == [("brandan", 20), ("partner", 20)]
    assert score(D(2026, 12, 3)) == []  # not paid twice


def test_log_triggers_scoring(client, auth, headers, players, put, monkeypatch):
    good_week(put)
    monkeypatch.setattr(main, "_today", lambda: D(2026, 11, 10))
    r = client.post("/log", json={"date": "2026-11-10", "minutes": 0, "calories": 0},
                    headers=headers("brandan"))
    assert r.status_code == 200
    assert balance(client, auth)["balance"] == 54


# ---------- goal history: minutes/calories apply that day, weekly target next Monday ----------


def set_today(monkeypatch, day):
    monkeypatch.setattr(main, "_today", lambda: day)


def test_first_save_replaces_defaults_later_saves_apply_that_day(client, auth, monkeypatch):
    set_today(monkeypatch, D(2026, 11, 11))  # a Wednesday
    first = client.patch("/api/me/settings", json={"min_calories": 500}, headers=auth).json()
    assert first["effective_from"] == "1970-01-01"

    second = client.patch("/api/me/settings", json={"min_calories": 300}, headers=auth).json()
    assert second["effective_from"] == "2026-11-11"
    me = client.get("/api/me", headers=auth).json()
    assert me["min_calories"] == 300


def test_weekly_target_can_change_any_time_during_the_week(client, auth, monkeypatch):
    set_today(monkeypatch, D(2026, 11, 9))
    client.patch("/api/me/settings", json={"weekly_workouts": 4}, headers=auth)
    set_today(monkeypatch, D(2026, 11, 12))  # Thursday
    client.patch("/api/me/settings", json={"weekly_workouts": 2}, headers=auth)
    assert client.get("/api/me", headers=auth).json()["weekly_workouts"] == 2


def test_several_saves_in_one_day_keep_only_the_last(client, auth, monkeypatch):
    set_today(monkeypatch, D(2026, 11, 11))
    client.patch("/api/me/settings", json={"min_calories": 500}, headers=auth)
    for cal in (300, 350, 275):
        client.patch("/api/me/settings", json={"min_calories": cal}, headers=auth)
    assert client.get("/api/me", headers=auth).json()["min_calories"] == 275
    with Session(engine) as db:
        assert len(db.scalars(select(main.UserGoal)).all()) == 2  # first + today


def test_calendar_judges_each_day_by_the_goal_then(client, auth, log, monkeypatch):
    set_today(monkeypatch, D(2026, 11, 3))
    client.patch("/api/me/settings", json={"min_calories": 200}, headers=auth)  # first save
    set_today(monkeypatch, D(2026, 11, 8))
    client.patch("/api/me/settings", json={"min_calories": 600}, headers=auth)  # from Nov 8
    for day in ("2026-11-04", "2026-11-07", "2026-11-08", "2026-11-10"):
        log(as_user="brandan", date=day, minutes=45, calories=300)
    days = client.get("/api/calendar?month=2026-11", headers=auth).json()["days"]
    assert days["2026-11-04"]["brandan"]["workout"] is True   # goal 200
    assert days["2026-11-07"]["brandan"]["workout"] is True   # goal 200
    assert days["2026-11-08"]["brandan"]["workout"] is False  # goal 600 from this day
    assert days["2026-11-10"]["brandan"]["workout"] is False


def test_calorie_goal_change_mid_week_applies_from_that_day(client, auth, players, put, monkeypatch):
    set_today(monkeypatch, D(2026, 11, 5))  # Thursday
    client.patch("/api/me/settings", json={"min_calories": 100}, headers=auth)
    good_week(put, MON)
    score(D(2026, 11, 10))
    daily = [e["points"] for e in client.get("/api/ledger?limit=1000", headers=auth).json()
             if e["category"] == "calorie_daily" and e["player"] == "brandan"]
    # Mon-Wed at goal 500 (750 cal = 2 pts each) = 6; Thu-Sun at goal 100 = 3 pts each = 12
    assert sum(daily) == 18 and len(daily) == 7  # one row per day


def test_weekly_target_counts_for_the_whole_week_and_locks_at_sunday(client, auth, players, put, monkeypatch):
    good_week(put, MON, brandan_workouts=3)
    good_week(put, MON + dt.timedelta(days=7), brandan_workouts=3)
    set_today(monkeypatch, D(2026, 11, 5))  # Thursday of week 1: lower the target 4 -> 2
    client.patch("/api/me/settings", json={"weekly_workouts": 2}, headers=auth)
    set_today(monkeypatch, D(2026, 11, 9))  # Monday of week 2: raise it again, 2 -> 4
    client.patch("/api/me/settings", json={"weekly_workouts": 4}, headers=auth)
    score(D(2026, 11, 17))
    with Session(engine) as db:
        weeks = {p.period: p.pair_ok for p in db.scalars(select(main.ScoringPeriod))
                 if p.kind == "week"}
    # week 1 needed 2 (the Thursday save counts for the whole week and the Monday
    # save does not reach back); week 2 needs 4 and brandan only did 3
    assert weeks == {"wk-2026-11-02": True, "wk-2026-11-09": False}


def test_scoring_switch_off_pays_nothing(client, auth, players, put, monkeypatch):
    good_week(put)
    monkeypatch.setattr(main, "SCORING_ENABLED", False)
    assert score(D(2026, 11, 20)) == []
    r = client.post("/api/scoring/run", headers=auth).json()
    assert r["enabled"] is False and r["scored"] == []
    assert balance(client, auth)["balance"] == 0
    monkeypatch.setattr(main, "SCORING_ENABLED", True)  # data kept; flips on cleanly
    assert score(D(2026, 11, 20)) == ["wk-2026-11-02"]


# ---------- daily payment: days pay as they settle, weeks top up and add bonuses ----------


def ledger_rows(client, auth):
    return client.get("/api/ledger?limit=1000", headers=auth).json()


def test_days_pay_two_days_after_they_end(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 6))  # Friday: Mon, Tue, Wed have settled
    b = balance(client, auth)
    # brandan: 3 workout days at 150% = 3 x (2 + 2) = 12
    # partner: Nov 2, 3 at 150% + workout = 4 each, Nov 4 at 100% = 1  -> 9
    assert b["earned_by"] == {"brandan": 12, "partner": 9}
    assert all(e["period"].startswith("day-") for e in ledger_rows(client, auth))


def test_daily_payment_is_idempotent(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 6))
    first = balance(client, auth)["balance"]
    score(D(2026, 11, 6))
    score(D(2026, 11, 7))  # one more day settles, nothing already paid is repeated
    assert balance(client, auth)["balance"] > first
    keys = [(e["player"], e["period"], e["category"]) for e in ledger_rows(client, auth)]
    assert len(keys) == len(set(keys))


def test_week_close_total_is_the_same_as_paying_only_at_the_end(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 6))
    score(D(2026, 11, 8))
    score(D(2026, 11, 10))
    b = balance(client, auth)
    assert b["balance"] == 54 and b["earned_by"] == {"brandan": 30, "partner": 24}


def test_late_increase_after_a_day_was_paid_is_topped_up_at_close(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 6))  # Nov 2 already paid at 150%
    with engine.begin() as conn:  # a late sync raises partner's Nov 2 to 250% of goal
        conn.execute(main.text(
            "UPDATE daily_activity SET active_calories = 1000 "
            "WHERE date = '2026-11-02' AND user_id = (SELECT id FROM users WHERE name = 'partner')"))
    score(D(2026, 11, 10))
    assert balance(client, auth)["balance"] == 55  # 54 plus one extra tier point


def test_backfilled_old_weeks_get_one_row_per_day_like_live_days(client, auth, players, put):
    good_week(put)
    score(D(2026, 12, 1))
    rows = ledger_rows(client, auth)
    daily = [e for e in rows if e["category"] in ("calorie_daily", "workout_day")]
    assert daily and all(e["period"].startswith("day-") for e in daily)  # no week-stamped daily rows
    assert not any(e["note"] == "Late Update" for e in rows)             # nothing needed topping up
    assert balance(client, auth)["balance"] == 54                          # same total as ever
    keys = [(e["player"], e["period"], e["category"]) for e in rows]
    assert len(keys) == len(set(keys))


def test_late_update_is_labelled(client, auth, players, put):
    good_week(put)
    score(D(2026, 11, 6))
    with engine.begin() as conn:
        conn.execute(main.text(
            "UPDATE daily_activity SET active_calories = 1000 "
            "WHERE date = '2026-11-02' AND user_id = (SELECT id FROM users WHERE name = 'partner')"))
    score(D(2026, 11, 10))
    late = [e for e in ledger_rows(client, auth) if e["note"] == "Late Update"]
    assert len(late) == 1 and late[0]["points"] == 1
    assert late[0]["what"].startswith("Daily Calorie Goal Reached: Late Update \u00b7 Week of Nov 2")
