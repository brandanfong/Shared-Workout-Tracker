import pytest


def test_me_returns_default_goals(client, auth):
    r = client.get("/api/me", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"name": "brandan", "min_minutes": 30, "min_calories": 400,
                        "weekly_workouts": 2, "scoring_enabled": True}


def test_me_requires_key(client):
    assert client.get("/api/me").status_code == 401


def test_updating_goals_requires_key(client):
    r = client.patch("/api/me/settings", json={"min_minutes": 45})
    assert r.status_code == 401


def test_update_goals(client, auth):
    r = client.patch(
        "/api/me/settings", json={"min_minutes": 45, "min_calories": 500}, headers=auth
    )
    assert r.status_code == 200
    assert r.json() == {"name": "brandan", "min_minutes": 45, "min_calories": 500,
                        "weekly_workouts": 2, "effective_from": "1970-01-01"}
    me = client.get("/api/me", headers=auth).json()
    assert (me["min_minutes"], me["min_calories"]) == (45, 500)


def test_partial_update_keeps_the_other_value(client, auth):
    client.patch("/api/me/settings", json={"min_minutes": 45}, headers=auth)
    me = client.get("/api/me", headers=auth).json()
    assert me["min_minutes"] == 45
    assert me["min_calories"] == 400


def test_second_update_keeps_earlier_changes(client, auth):
    client.patch("/api/me/settings", json={"min_minutes": 45}, headers=auth)
    client.patch("/api/me/settings", json={"min_calories": 250}, headers=auth)
    me = client.get("/api/me", headers=auth).json()
    assert (me["min_minutes"], me["min_calories"]) == (45, 250)


def test_empty_update_changes_nothing(client, auth):
    r = client.patch("/api/me/settings", json={}, headers=auth)
    assert r.status_code == 200
    assert (r.json()["min_minutes"], r.json()["min_calories"]) == (30, 400)


def test_goals_are_per_person(client, headers):
    client.patch("/api/me/settings", json={"min_calories": 250}, headers=headers("brandan"))
    assert client.get("/api/me", headers=headers("brandan")).json()["min_calories"] == 250
    assert client.get("/api/me", headers=headers("sam")).json()["min_calories"] == 400


@pytest.mark.parametrize(
    "body",
    [
        {"min_minutes": 0},
        {"min_minutes": -5},
        {"min_minutes": 5000},
        {"min_calories": 0},
        {"min_calories": 99999},
        {"min_minutes": "abc"},
        {"min_calories": 12.5},
    ],
)
def test_invalid_goals_are_rejected(client, auth, body):
    assert client.patch("/api/me/settings", json=body, headers=auth).status_code == 422


def test_calendar_uses_each_persons_own_goals(client, headers, log):
    client.patch("/api/me/settings", json={"min_calories": 200}, headers=headers("brandan"))
    log(as_user="brandan", minutes=45, calories=250)
    log(as_user="sam", minutes=45, calories=250)

    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    day = data["days"]["2026-11-08"]
    assert day["brandan"]["workout"] is True   # his goal is 200 calories
    assert day["sam"]["workout"] is False      # her goal is still the default 400
    assert data["goals"]["brandan"]["min_calories"] == 200
    assert data["goals"]["sam"]["min_calories"] == 400


def test_calendar_lists_goals_for_people_with_no_activity(client, headers):
    headers("brandan")
    headers("sam")
    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    assert sorted(data["goals"]) == ["brandan", "sam"]
    assert data["days"] == {}

def test_me_reports_when_scoring_is_paused(client, auth, monkeypatch):
    import main
    monkeypatch.setattr(main, "SCORING_ENABLED", False)
    assert client.get("/api/me", headers=auth).json()["scoring_enabled"] is False
