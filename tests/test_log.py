def test_log_creates_user_and_row(log, rows):
    r = log()
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert rows() == [
        {"name": "brandan", "date": "2026-11-08",
         "exercise_minutes": 45, "active_calories": 400}
    ]


def test_name_is_trimmed_and_lowercased(log, rows):
    log(name="  BRANDAN ")
    log(name="brandan")
    assert len(rows()) == 1
    assert rows()[0]["name"] == "brandan"


def test_repeat_sync_updates_instead_of_duplicating(log, rows):
    log()
    log()
    assert len(rows()) == 1


def test_higher_values_overwrite(log, rows):
    log(minutes=45, calories=400)
    log(minutes=60, calories=500)
    assert rows()[0]["exercise_minutes"] == 60
    assert rows()[0]["active_calories"] == 500


def test_lower_values_do_not_reduce_totals(log, rows):
    log(minutes=60, calories=500)
    log(minutes=10, calories=100)
    assert rows()[0]["exercise_minutes"] == 60
    assert rows()[0]["active_calories"] == 500


def test_greatest_is_applied_per_column(log, rows):
    log(minutes=60, calories=100)
    log(minutes=30, calories=500)
    assert rows()[0]["exercise_minutes"] == 60
    assert rows()[0]["active_calories"] == 500


def test_decimals_are_rounded(log, rows):
    r = log(minutes=44.6, calories=399.4)
    assert r.status_code == 200
    assert rows()[0]["exercise_minutes"] == 45
    assert rows()[0]["active_calories"] == 399


def test_numeric_strings_are_accepted(log, rows):
    r = log(minutes="45", calories="400")
    assert r.status_code == 200
    assert rows()[0]["exercise_minutes"] == 45


def test_two_users_same_day_get_separate_rows(log, rows):
    log(name="brandan")
    log(name="sam", minutes=20, calories=150)
    assert len(rows()) == 2


def test_same_user_different_days_get_separate_rows(log, rows):
    log(date="2026-11-07")
    log(date="2026-11-08")
    assert len(rows()) == 2


def test_wrong_key_is_401(client):
    body = {"name": "brandan", "date": "2026-11-08", "minutes": 45, "calories": 400}
    r = client.post("/log", json=body, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_missing_key_is_401(client):
    body = {"name": "brandan", "date": "2026-11-08", "minutes": 45, "calories": 400}
    r = client.post("/log", json=body)
    assert r.status_code == 401


def test_negative_minutes_rejected(log, rows):
    assert log(minutes=-5).status_code == 422
    assert rows() == []


def test_absurd_minutes_rejected(log):
    assert log(minutes=5000).status_code == 422


def test_non_numeric_value_rejected(log):
    assert log(minutes="abc").status_code == 422


def test_bad_date_rejected(log):
    assert log(date="11/08/2026").status_code == 422


def test_missing_name_rejected(client, auth):
    body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
    assert client.post("/log", json=body, headers=auth).status_code == 422