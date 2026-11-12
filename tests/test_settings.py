import datetime as dt

import pytest
from conftest import backdate_goals


def test_me_returns_default_goals(client, auth):
    r = client.get("/api/me", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"name": "brandan", "min_minutes": 30, "min_calories": 400,
                        "weekly_workouts": 2, "goals_set": False,
                        "scoring_enabled": True, "color": "#3b82f6"}


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
                        "weekly_workouts": 2, "effective_from": dt.date.today().isoformat()}
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
    backdate_goals()
    log(as_user="brandan", minutes=45, calories=250)
    log(as_user="sam", minutes=45, calories=250)

    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    day = data["days"]["2026-11-08"]
    assert day["brandan"]["workout"] is True   # his goal is 200 calories
    assert day["sam"]["workout"] is False      # she has no goals yet, so nothing is judged
    assert day["sam"]["goals_set"] is False
    assert data["goals"]["brandan"]["min_calories"] == 200
    assert data["goals"]["sam"] is None


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


# ---------- colors ----------


def test_default_colors_are_automatic_and_distinct(client, headers):
    headers("brandan")
    headers("sam")
    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    assert data["colors"]["brandan"] != data["colors"]["sam"]
    me = client.get("/api/me", headers=headers("brandan")).json()
    assert me["color"] == data["colors"]["brandan"]


def test_pick_a_color_and_it_shows_everywhere(client, headers):
    headers("sam")
    r = client.patch("/api/me/color", json={"color": "#EF4444"}, headers=headers("brandan"))
    assert r.status_code == 200 and r.json() == {"color": "#ef4444", "custom": True}
    data = client.get("/api/calendar?month=2026-11", headers=headers("sam")).json()
    assert data["colors"]["brandan"] == "#ef4444"


def test_color_can_go_back_to_automatic(client, headers):
    client.patch("/api/me/color", json={"color": "#ef4444"}, headers=headers("brandan"))
    r = client.patch("/api/me/color", json={"color": None}, headers=headers("brandan"))
    assert r.json()["custom"] is False and r.json()["color"] != "#ef4444"


@pytest.mark.parametrize("bad", ["red", "#12", "#808080", "#ffd0d0", "#200040"])
def test_bad_colors_rejected(client, auth, bad):
    r = client.patch("/api/me/color", json={"color": bad}, headers=auth)
    assert r.status_code == 422 and r.json()["detail"]


def test_cannot_pick_a_color_too_close_to_someone_else(client, headers):
    client.patch("/api/me/color", json={"color": "#3b82f6"}, headers=headers("brandan"))
    r = client.patch("/api/me/color", json={"color": "#3c83f6"}, headers=headers("sam"))
    assert r.status_code == 422 and "Brandan" in r.json()["detail"]
    # the same person can re-save their own color
    assert client.patch("/api/me/color", json={"color": "#3b82f6"},
                        headers=headers("brandan")).status_code == 200


def test_color_options_mark_taken_swatches_unavailable(client, headers):
    client.patch("/api/me/color", json={"color": "#3b82f6"}, headers=headers("brandan"))
    opts = client.get("/api/colors", headers=headers("sam")).json()
    by = {p["color"]: p["available"] for p in opts["presets"]}
    assert by["#3b82f6"] is False and by["#f97316"] is True
    mine = client.get("/api/colors", headers=headers("brandan")).json()
    assert {p["color"]: p["available"] for p in mine["presets"]}["#3b82f6"] is True


def test_color_endpoints_require_a_key(client):
    assert client.get("/api/colors").status_code == 401
    assert client.patch("/api/me/color", json={"color": "#ef4444"}).status_code == 401


def test_calendar_sends_the_everyone_color(client, headers):
    import colors
    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    assert data["everyone_color"] == colors.EVERYONE_COLOR


def test_cannot_pick_the_everyone_color(client, auth):
    r = client.patch("/api/me/color", json={"color": "#16a34a"}, headers=auth)
    assert r.status_code == 422 and "everyone" in r.json()["detail"]


def test_me_reports_goals_set_after_first_save(client, auth):
    assert client.get("/api/me", headers=auth).json()["goals_set"] is False
    client.patch("/api/me/settings", json={"min_minutes": 45}, headers=auth)
    assert client.get("/api/me", headers=auth).json()["goals_set"] is True


def test_calendar_hides_judgement_until_goals_are_set(client, headers, log, set_goals):
    set_goals("brandan")
    log(as_user="brandan", minutes=60, calories=900)
    log(as_user="sam", minutes=60, calories=900)
    data = client.get("/api/calendar?month=2026-11", headers=headers("brandan")).json()
    day = data["days"]["2026-11-08"]
    assert day["brandan"]["workout"] is True
    sam = day["sam"]
    assert sam["exercise_minutes"] == 60 and sam["active_calories"] == 900  # raw data still shown
    assert (sam["workout"], sam["calorie_hit"], sam["minutes_hit"]) == (False, False, False)
    assert data["goals"]["sam"] is None


# ---------- automatic colors stay put ----------


def test_new_people_get_the_first_free_color_in_order_added(client, headers):
    # Alex, then Carl, then Bruce: Bruce sorts between them but must not take Carl's color.
    alex = client.get("/api/me", headers=headers("alex")).json()["color"]
    carl = client.get("/api/me", headers=headers("carl")).json()["color"]
    bruce = client.get("/api/me", headers=headers("bruce")).json()["color"]
    assert len({alex, carl, bruce}) == 3
    assert client.get("/api/me", headers=headers("carl")).json()["color"] == carl
    assert client.get("/api/me", headers=headers("alex")).json()["color"] == alex
