from sqlalchemy import text

from app.db import engine


def test_missing_key_is_401(client):
    body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
    assert client.post("/log", json=body).status_code == 401


def test_unknown_key_is_401(client):
    body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
    r = client.post("/log", json=body, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_key_decides_the_user_not_the_body(client, headers, rows):
    # An old Shortcut still sends "name". It must not let one person write
    # into another person's data.
    body = {"name": "sam", "date": "2026-11-08", "minutes": 45, "calories": 400}
    r = client.post("/log", json=body, headers=headers("brandan"))
    assert r.status_code == 200
    assert [row["name"] for row in rows()] == ["brandan"]


def test_each_key_writes_only_its_own_rows(log, rows):
    log(as_user="brandan", minutes=45, calories=400)
    log(as_user="sam", minutes=20, calories=150)
    by_name = {r["name"]: r for r in rows()}
    assert by_name["brandan"]["exercise_minutes"] == 45
    assert by_name["sam"]["exercise_minutes"] == 20


def test_revoked_key_is_rejected(client, auth):
    body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
    assert client.post("/log", json=body, headers=auth).status_code == 200
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET revoked_at = now()"))
    assert client.post("/log", json=body, headers=auth).status_code == 401


def test_revoking_one_key_leaves_the_others_working(client, make_key):
    old = make_key("brandan", "old phone")
    new = make_key("brandan", "new phone")
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET revoked_at = now() WHERE label = 'old phone'"))
    body = {"date": "2026-11-08", "minutes": 45, "calories": 400}
    assert client.post("/log", json=body, headers={"X-API-Key": old}).status_code == 401
    assert client.post("/log", json=body, headers={"X-API-Key": new}).status_code == 200


def test_keys_are_stored_hashed_not_raw(make_key):
    key = make_key("brandan")
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT key_hash FROM api_keys")).scalar_one()
    assert stored != key
    assert key not in stored
    assert len(stored) == 64  # sha-256 hex


def test_issue_key_normalizes_name_and_reuses_the_person(make_key):
    make_key("  BRANDAN ")
    make_key("brandan")
    with engine.connect() as conn:
        users = conn.execute(text("SELECT name FROM users")).scalars().all()
        keys = conn.execute(text("SELECT count(*) FROM api_keys")).scalar_one()
    assert users == ["brandan"]
    assert keys == 2


def test_either_persons_key_can_read_the_shared_dashboard(client, headers, log):
    log(as_user="brandan")
    log(as_user="sam")
    for person in ("brandan", "sam"):
        r = client.get("/api/calendar?month=2026-11", headers=headers(person))
        assert r.status_code == 200
        assert r.json()["users"] == ["brandan", "sam"]