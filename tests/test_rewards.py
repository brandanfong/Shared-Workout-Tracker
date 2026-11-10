import pytest
from sqlalchemy.orm import Session

from main import engine, record_earn, User
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
