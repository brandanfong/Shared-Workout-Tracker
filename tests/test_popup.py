"""The points popup: GET /api/popup and POST /api/popup/seen."""
import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import engine
from app.models import DailyActivity, User
from app.scoring_runner import run_scoring
from conftest import backdate_goals

D = dt.date
MON = D(2026, 11, 2)
B_WORKOUT = (45, 750)  # brandan: 150% of his 500 goal and a workout day
B_HIT = (0, 500)       # exactly 100%, no workout


@pytest.fixture(autouse=True)
def start_nov_1(monkeypatch):
    monkeypatch.setattr(config, "SCORING_START", D(2026, 11, 1))


@pytest.fixture
def players(client, headers):
    """brandan: 500 cal, 4 workouts a week. partner: 400 cal, 2 a week."""
    client.patch("/api/me/settings", headers=headers("brandan"),
                 json={"min_calories": 500, "weekly_workouts": 4})
    client.patch("/api/me/settings", headers=headers("partner"),
                 json={"min_calories": 400, "weekly_workouts": 2})
    backdate_goals()


def put(name, first_day, per_day):
    with Session(engine) as db:
        uid = db.scalar(select(User.id).where(User.name == name))
        for i, (mins, cal) in enumerate(per_day):
            day = first_day + dt.timedelta(days=i)
            old = db.scalar(select(DailyActivity).where(DailyActivity.user_id == uid, DailyActivity.date == day))
            if old is not None:
                db.delete(old)
                db.flush()
            db.add(DailyActivity(user_id=uid, date=day, exercise_minutes=mins, active_calories=cal))
        db.commit()


def brandan_week(monday=MON, workouts=4):
    put("brandan", monday, [B_WORKOUT] * workouts + [B_HIT] * (7 - workouts))


def partner_week(monday=MON, good=True):
    put("partner", monday, [(45, 600)] * 2 + [(0, 400)] * 5 if good else [(0, 100)] * 7)


def score(today):
    with Session(engine) as db:
        run_scoring(db, today)


def get(client, auth):
    r = client.get("/api/popup", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def close(client, auth):
    """Open the popup and click Got it. Returns what was shown (or None)."""
    body = get(client, auth)
    if body["popup"]:
        r = client.post("/api/popup/seen", json=body["ack"], headers=auth)
        assert r.status_code == 204, r.text
    return body["popup"]


def names(p):
    return [r["name"] for r in p["rows"]]


def week_so_far_seen(client, auth):
    """Both played the week; by Sunday Mon-Sat are paid and brandan has seen them."""
    brandan_week()
    partner_week()
    score(D(2026, 11, 8))
    assert close(client, auth) is not None


def test_needs_a_key(client):
    assert client.get("/api/popup").status_code == 401


def test_nothing_new_means_no_popup(client, auth, players):
    assert get(client, auth) == {"popup": None}


def test_first_points_then_one_day(client, auth, players):
    put("brandan", MON, [B_WORKOUT, B_HIT])
    score(D(2026, 11, 3))  # Monday is final (Tuesday row exists)
    p = close(client, auth)
    assert p["title"] == "Your first points are in!"
    assert p["total"] == 4
    assert p["rows"] == [
        {"name": "Daily calorie goals", "points": 2, "sub": "Mon, Nov 2, 150% of goal"},
        {"name": "Complete workouts", "points": 2, "sub": "Mon, Nov 2"},
    ]
    assert get(client, auth) == {"popup": None}  # closing it marks it seen

    put("brandan", D(2026, 11, 4), [B_HIT])
    score(D(2026, 11, 4))
    p = close(client, auth)
    assert p["title"] == "Your points for Tue, Nov 3"
    assert p["total"] == 1 and p["highlights"] == []


def test_several_days_are_grouped(client, auth, players):
    put("brandan", MON, [B_WORKOUT])
    score(D(2026, 11, 2))
    put("brandan", D(2026, 11, 3), [B_HIT])
    score(D(2026, 11, 3))
    close(client, auth)  # first points (Monday)
    put("brandan", D(2026, 11, 4), [B_WORKOUT, (90, 1000), B_HIT])
    score(D(2026, 11, 6))
    p = get(client, auth)["popup"]
    assert p["title"] == "While you were away (Nov 3 – Nov 5)"
    assert p["rows"][0] == {"name": "Daily calorie goals", "points": 6, "sub": "3 days, best 200%"}
    assert p["rows"][1] == {"name": "Complete workouts", "points": 4, "sub": "2 days"}


def test_your_week_then_team_result(client, auth, players):
    week_so_far_seen(client, auth)
    put("brandan", D(2026, 11, 9), [(0, 0)])  # brandan syncs Monday, partner hasn't
    score(D(2026, 11, 9))
    p = close(client, auth)
    assert p["title"] == "Your week is in"
    assert p["total"] == 10  # Sunday +1, 7 of 7 days +5, workout goal +4
    assert names(p) == ["Daily calorie goals", "Weekly calorie bonus", "Workout goal", "Team bonus"]
    assert p["rows"][1]["sub"] == "7 of 7 days"
    assert p["rows"][2]["sub"] == "4 of 4 workouts"
    assert p["rows"][3]["points"] is None  # pending
    assert p["highlights"] == [{"tone": "perfect", "title": "You hit both weekly goals!",
                                "detail": "Perfect calorie week.", "hint": None}]

    put("partner", D(2026, 11, 9), [(0, 0)])
    score(D(2026, 11, 9))
    p = close(client, auth)
    assert p["title"] == "Team result is in"
    assert p["total"] == 4 and names(p) == ["Team bonus"]
    assert p["highlights"] == [{"tone": "gold", "title": "Team week succeeded!", "detail": "1-week streak",
                                "hint": "Win next week to unlock x1.1"}]


def test_both_parts_in_one_visit(client, auth, players):
    week_so_far_seen(client, auth)
    put("brandan", D(2026, 11, 9), [(0, 0)])
    put("partner", D(2026, 11, 9), [(0, 0)])
    score(D(2026, 11, 9))
    p = get(client, auth)["popup"]
    assert p["title"] == "Last week is in"
    assert [h["title"] for h in p["highlights"]] == ["Team week succeeded!", "You hit both weekly goals!"]
    assert "Team bonus" in names(p) and all(r["points"] is not None for r in p["rows"])


def test_you_did_not_qualify(client, auth, players):
    brandan_week(workouts=3)
    partner_week()
    score(D(2026, 11, 8))
    close(client, auth)
    put("brandan", D(2026, 11, 9), [(0, 0)])
    put("partner", D(2026, 11, 9), [(0, 0)])
    score(D(2026, 11, 9))
    p = get(client, auth)["popup"]
    assert p["title"] == "Last week is in"
    assert p["highlights"] == [{"tone": "plain", "title": "You didn’t qualify this week",
                                "detail": "3 of 4 workouts", "hint": None}]
    assert "Team bonus" not in names(p)


def test_a_missed_team_week_rides_along_with_the_next_popup(client, auth, players):
    brandan_week()
    partner_week(good=False)
    score(D(2026, 11, 8))
    close(client, auth)
    put("brandan", D(2026, 11, 9), [B_WORKOUT])
    score(D(2026, 11, 9))
    assert close(client, auth)["title"] == "Your week is in"

    put("partner", D(2026, 11, 9), [(0, 0)])
    score(D(2026, 11, 9))
    assert get(client, auth) == {"popup": None}  # a miss alone pays nothing: no popup

    put("brandan", D(2026, 11, 10), [(0, 0)])
    score(D(2026, 11, 10))  # Monday Nov 9 is final now
    p = close(client, auth)
    assert p["title"] == "Your points for Mon, Nov 9"
    assert p["highlights"] == [{"tone": "plain", "title": "Last week’s team result: didn’t qualify",
                                "detail": "Your own points still counted.", "hint": None}]


def test_streak_level_up(client, auth, players):
    for monday in (MON, MON + dt.timedelta(days=7)):
        brandan_week(monday)
        partner_week(monday)
    score(D(2026, 11, 15))
    close(client, auth)
    put("brandan", D(2026, 11, 16), [(0, 0)])
    put("partner", D(2026, 11, 16), [(0, 0)])
    score(D(2026, 11, 16))
    p = get(client, auth)["popup"]
    assert p["highlights"][0] == {"tone": "gold", "title": "Streak unlocked: x1.1!",
                                  "detail": "2-week streak · last week’s points +10%",
                                  "hint": "2 more weeks for x1.25"}
    assert "Streak multiplier" in names(p)


def test_away_two_weeks(client, auth, players):
    put("brandan", MON, [B_WORKOUT, B_HIT])
    score(D(2026, 11, 3))
    close(client, auth)
    for monday in (MON, MON + dt.timedelta(days=7)):
        brandan_week(monday)
        partner_week(monday)
    put("brandan", D(2026, 11, 16), [(0, 0)])
    put("partner", D(2026, 11, 16), [(0, 0)])
    score(D(2026, 11, 16))
    p = get(client, auth)["popup"]
    assert p["title"] == "While you were away (Nov 3 – Nov 15)"
    assert p["highlights"][0]["title"] == "The team qualified 2 of 2 weeks"
    assert p["highlights"][0]["detail"] == "2-week streak · x1.1"
    team = next(r for r in p["rows"] if r["name"] == "Team bonus")
    assert team["sub"] == "2 weeks"


def test_month_closed(client, auth, players):
    nov = [D(2026, 11, 1) + dt.timedelta(days=i) for i in range(30)]
    put("brandan", nov[0], [B_HIT] * 30)
    put("partner", nov[0], [(0, 400)] * 30)
    score(D(2026, 11, 30))
    close(client, auth)
    put("brandan", D(2026, 12, 1), [(0, 0)])
    score(D(2026, 12, 1))
    p = get(client, auth)["popup"]
    assert p["title"] == "November is closed"
    assert p["highlights"] == [{"tone": "perfect", "title": "Perfect month! +20",
                                "detail": "You hit your calorie goal every single day.", "hint": None}]
    assert p["rows"][-1] == {"name": "Monthly calorie bonus", "points": 20, "sub": "30 of 30 days (100%)"}


def test_teammates_points_do_not_open_my_popup(client, auth, headers, players):
    put("partner", MON, [(45, 600), (0, 0)])
    score(D(2026, 11, 3))
    assert get(client, auth) == {"popup": None}
    assert get(client, headers("partner"))["popup"] is not None


def test_seen_never_moves_back_and_ignores_junk(client, auth, players):
    put("brandan", MON, [B_WORKOUT, B_HIT])
    score(D(2026, 11, 3))
    close(client, auth)
    r = client.post("/api/popup/seen", json={"ledger_id": 0, "my_week": "nonsense", "team_week": None},
                    headers=auth)
    assert r.status_code == 204
    assert get(client, auth) == {"popup": None}
