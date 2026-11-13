import pytest
from app import config
from sqlalchemy.orm import Session

from app.db import engine
from app.ledger import record_earn
from app.models import User
from sqlalchemy import select, text


def _uid(name):
    with Session(engine) as db:
        return db.scalar(select(User.id).where(User.name == name))


@pytest.fixture
def earn(headers):
    """Credit points to a person the way the scoring job will."""

    def _earn(points, name="brandan", period="wk-2026-11-02", category="workout_day"):
        headers(name)  # make sure the person exists
        with Session(engine) as db:
            return record_earn(db, _uid(name), period, category, points)

    return _earn


@pytest.fixture
def reward(client, auth):
    def _reward(**overrides):
        body = {"name": "Coffee", "cost": 50}
        body.update(overrides)
        r = client.post("/api/rewards", json=body, headers=auth)
        assert r.status_code == 201, r.text
        return r.json()

    return _reward


def test_requires_key(client):
    assert client.get("/api/rewards").status_code == 401
    assert client.get("/api/ledger").status_code == 401
    assert client.get("/api/balance").status_code == 401
    assert client.post("/api/rewards/1/redeem").status_code == 401


def test_empty_balance(client, auth):
    assert client.get("/api/balance", headers=auth).json() == {
        "balance": 0,
        "earned_by": {},
    }


def test_earn_updates_shared_balance_and_per_person(client, auth, headers, earn):
    earn(10, "brandan")
    earn(7, "partner")
    body = client.get("/api/balance", headers=auth).json()
    assert body["balance"] == 17
    assert body["earned_by"] == {"brandan": 10, "partner": 7}


def test_earn_is_idempotent(client, auth, earn):
    assert earn(10) is not None
    assert earn(10) is None  # same person + period + category: no double pay
    assert client.get("/api/balance", headers=auth).json()["balance"] == 10
    # a different category in the same week still pays
    assert earn(5, category="calorie_daily") is not None
    assert client.get("/api/balance", headers=auth).json()["balance"] == 15


def test_add_and_edit_reward(client, auth, reward):
    r = reward(name="  Spa  ", cost=500, cooldown_days=30)
    assert r["name"] == "Spa" and r["added_by"] == "brandan"

    p = client.patch(f"/api/rewards/{r['id']}", json={"cost": 450}, headers=auth)
    assert p.json()["cost"] == 450

    client.patch(f"/api/rewards/{r['id']}", json={"active": False}, headers=auth)
    assert client.get("/api/rewards", headers=auth).json() == []
    listed = client.get("/api/rewards?include_inactive=true", headers=auth).json()
    assert [x["id"] for x in listed] == [r["id"]]


@pytest.mark.parametrize("cost", [0, -5, 5001])
def test_reward_cost_bounds(client, auth, cost):
    r = client.post("/api/rewards", json={"name": "x", "cost": cost}, headers=auth)
    assert r.status_code == 422


def test_update_missing_reward(client, auth):
    assert client.patch("/api/rewards/999", json={"cost": 5}, headers=auth).status_code == 404


def test_redeem_deducts_and_logs_who(client, auth, headers, earn, reward):
    earn(100, "brandan")
    r = reward(cost=40)
    out = client.post(f"/api/rewards/{r['id']}/redeem", headers=headers("partner"))
    assert out.status_code == 200
    assert out.json()["player"] == "partner"
    assert out.json()["points"] == -40
    assert out.json()["balance_after"] == 60
    assert client.get("/api/balance", headers=auth).json()["balance"] == 60

    redeems = client.get("/api/ledger?type=redeem", headers=auth).json()
    assert len(redeems) == 1
    assert redeems[0]["player"] == "partner" and redeems[0]["reward"] == "Coffee"


def test_redeem_needs_enough_points(client, auth, earn, reward):
    earn(10)
    r = reward(cost=50)
    out = client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)
    assert out.status_code == 409
    assert client.get("/api/balance", headers=auth).json()["balance"] == 10


def test_cannot_redeem_inactive_or_missing(client, auth, earn, reward):
    earn(100)
    r = reward()
    client.patch(f"/api/rewards/{r['id']}", json={"active": False}, headers=auth)
    assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 404
    assert client.post("/api/rewards/999/redeem", headers=auth).status_code == 404


def test_one_time_reward(client, auth, earn, reward):
    earn(100)
    r = reward(cost=10, repeatable=False)
    assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 200
    assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 409


def test_cooldown(client, auth, earn, reward):
    earn(100)
    r = reward(cost=10, cooldown_days=7)
    assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 200
    again = client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)
    assert again.status_code == 409 and "cooldown" in again.json()["detail"]

    # age the previous redemption past the cooldown
    with engine.begin() as conn:
        conn.execute(text("UPDATE ledger SET created_at = now() - interval '8 days' WHERE type = 'redeem'"))
    assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 200


def test_no_cooldown_allows_repeat(client, auth, earn, reward):
    earn(100)
    r = reward(cost=10)
    for _ in range(3):
        assert client.post(f"/api/rewards/{r['id']}/redeem", headers=auth).status_code == 200
    assert client.get("/api/balance", headers=auth).json()["balance"] == 70


def test_adjust_requires_note_and_floor(client, auth, earn):
    earn(20)
    assert client.post("/api/adjust", json={"points": 5}, headers=auth).status_code == 422
    ok = client.post("/api/adjust", json={"points": -5, "note": "double counted"}, headers=auth)
    assert ok.status_code == 201 and ok.json()["balance_after"] == 15
    too_far = client.post("/api/adjust", json={"points": -50, "note": "oops"}, headers=auth)
    assert too_far.status_code == 409
    assert client.get("/api/balance", headers=auth).json()["balance"] == 15


def test_ledger_filters_and_running_balance(client, auth, headers, earn, reward):
    earn(30, "brandan")
    earn(20, "partner")
    r = reward(cost=10)
    client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)

    everything = client.get("/api/ledger", headers=auth).json()
    assert [e["balance_after"] for e in reversed(everything)] == [30, 50, 40]

    assert len(client.get("/api/ledger?type=earn", headers=auth).json()) == 2
    mine = client.get("/api/ledger?player=Partner", headers=auth).json()
    assert [e["player"] for e in mine] == ["partner"]
    assert client.get("/api/ledger?type=bogus", headers=auth).status_code == 422


def test_ledger_rejects_bad_rows_at_db_level():
    """Sign rules are enforced by the database, not only by the API."""
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as db:
        u = User(name="x")
        db.add(u)
        db.flush()
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO ledger (type, user_id, category, points, balance_after) "
                    "VALUES ('earn', :u, 'workout_day', -5, 0)"
                ),
                {"u": u.id},
            )


# ---------- history: month filter, search, paging ----------


def _months(client, auth):
    return client.get("/api/ledger/months", headers=auth).json()


def test_month_filter_uses_the_month_a_day_was_earned_for(client, auth, earn):
    # A day is paid two days late: the 30th is written on the 2nd but belongs to November.
    earn(5, period="day-2026-11-30", category="calorie_daily")
    earn(7, period="day-2026-12-01", category="calorie_daily")
    earn(9, period="wk-2026-10-26", category="workout_goal")
    earn(11, period="2026-10", category="calorie_monthly")

    def points(month):
        rows = client.get(f"/api/ledger?month={month}", headers=auth).json()
        return sorted(e["points"] for e in rows)

    assert points("2026-11") == [5]
    assert points("2026-12") == [7]
    assert points("2026-10") == [9, 11]
    assert points("2025-01") == []


def test_redeems_and_adjustments_count_in_the_month_they_happened(client, auth, earn, reward):
    import datetime as dt
    earn(100, period="day-2026-01-05", category="calorie_daily")
    r = reward(cost=10)
    client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)
    this_month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    rows = client.get(f"/api/ledger?month={this_month}&type=redeem", headers=auth).json()
    assert len(rows) == 1


def test_months_list_is_distinct_and_newest_first(client, auth, earn):
    earn(1, period="day-2026-09-10", category="calorie_daily")
    earn(1, period="day-2026-09-11", category="calorie_daily")
    earn(1, period="day-2026-11-03", category="calorie_daily")
    earn(1, period="wk-2026-10-05", category="workout_goal")
    months = _months(client, auth)
    assert months == ["2026-11", "2026-10", "2026-09"]


def test_search_matches_reward_names_and_notes(client, auth, earn, reward):
    earn(100)
    r = reward(name="Pizza Night", cost=10)
    client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)
    client.post("/api/adjust", json={"points": -2, "note": "Double counted Tuesday"}, headers=auth)

    def found(q):
        return len(client.get("/api/ledger", params={"q": q}, headers=auth).json())

    assert found("pizza") == 1          # case-insensitive, reward name
    assert found("TUESDAY") == 1        # adjustment note
    assert found("nothing like this") == 0
    assert found("  pizza  ") == 1      # spaces around it are ignored


def test_search_treats_percent_and_underscore_literally(client, auth, earn, reward):
    earn(100)
    r = reward(name="Date Night", cost=10)
    client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)
    assert client.get("/api/ledger", params={"q": "%"}, headers=auth).json() == []
    assert client.get("/api/ledger", params={"q": "D_te"}, headers=auth).json() == []


def test_paging_with_before_has_no_gaps_or_repeats(client, auth, earn):
    for i in range(5):
        earn(i + 1, period=f"day-2026-11-0{i + 1}", category="calorie_daily")
    page1 = client.get("/api/ledger?limit=2", headers=auth).json()
    page2 = client.get(f"/api/ledger?limit=2&before={page1[-1]['id']}", headers=auth).json()
    page3 = client.get(f"/api/ledger?limit=2&before={page2[-1]['id']}", headers=auth).json()
    ids = [e["id"] for e in page1 + page2 + page3]
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 5


def test_filters_combine(client, auth, headers, earn):
    earn(5, "brandan", period="day-2026-11-03", category="calorie_daily")
    earn(6, "partner", period="day-2026-11-03", category="calorie_daily")
    earn(7, "partner", period="day-2026-10-03", category="calorie_daily")
    rows = client.get("/api/ledger?player=partner&month=2026-11&type=earn", headers=auth).json()
    assert [e["points"] for e in rows] == [6]


@pytest.mark.parametrize("params", ["month=2026-13", "month=2026-1", "month=abc", "limit=0", "before=0"])
def test_bad_history_options_are_rejected(client, auth, params):
    assert client.get(f"/api/ledger?{params}", headers=auth).status_code == 422


def test_history_months_requires_a_key(client):
    assert client.get("/api/ledger/months").status_code == 401


def test_search_finds_what_the_table_shows(client, auth, headers, earn, reward):
    import datetime as dt
    y = config.today().year
    earn(5, "brandan", period=f"day-{y}-11-04", category="calorie_daily")
    earn(6, "partner", period=f"wk-{y}-11-02", category="workout_goal")
    earn(7, "partner", period=f"{y}-10", category="calorie_monthly")
    earn(50, "brandan", period=f"day-{y}-11-05", category="workout_day")
    earn(60, "brandan", period=f"day-{y - 1}-03-09", category="calorie_daily")
    r = reward(name="Movie Night", cost=10)
    client.post(f"/api/rewards/{r['id']}/redeem", headers=auth)

    def pts(q):
        return sorted(e["points"] for e in client.get("/api/ledger", params={"q": q}, headers=auth).json())

    assert pts("daily calorie") == [5, 60]        # the wording shown in the table
    assert pts("calorie") == [5, 7, 60]           # partial word
    assert pts("complete workout") == [50]
    assert pts("weekly workout goal") == [6]
    assert pts(f"week of {y}-11-02") == [6]       # a week, ISO form
    assert pts(f"{y}-11-04") == [5]               # a day's date
    assert pts("nov 4") == [5]                    # short date as shown
    assert pts("november") == [5, 6, 50]          # month name of dated rows
    assert pts(f"october {y}") == [7]
    assert pts("mar 9") == [60]                   # last year's day
    assert pts(f"mar 9, {y - 1}") == [60]         # ...as shown, with its year
    assert pts("partner") == [6, 7]               # who
    assert pts("redeemed") == [-10]               # the type
    assert pts("movie") == [-10]                  # reward
    assert pts("nothing here") == []


def test_history_rows_carry_their_sentence(client, auth, earn):
    import datetime as dt
    y = config.today().year
    earn(5, "brandan", period=f"day-{y}-11-04", category="calorie_daily")
    earn(6, "brandan", period=f"day-{y - 1}-11-04", category="calorie_daily")
    rows = {e["points"]: e["what"] for e in client.get("/api/ledger", headers=auth).json()}
    assert rows[5] == "Daily Calorie Goal Reached \u00b7 Nov 4"
    assert rows[6] == f"Daily Calorie Goal Reached \u00b7 Nov 4, {y - 1}"
