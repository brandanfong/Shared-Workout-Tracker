def test_list_shows_only_my_keys_and_marks_the_current_one(client, make_key):
    mine = make_key("brandan", "laptop")
    make_key("brandan", "old phone")
    make_key("sam", "sams phone")
    rows = client.get("/api/keys", headers={"X-API-Key": mine}).json()
    assert sorted(r["label"] for r in rows) == ["laptop", "old phone"]
    assert [r["label"] for r in rows if r["current"]] == ["laptop"]
    assert all("key_hash" not in r for r in rows)


def test_revoke_other_key_stops_it_working(client, make_key):
    mine = make_key("brandan", "laptop")
    old = make_key("brandan", "old phone")
    rows = client.get("/api/keys", headers={"X-API-Key": mine}).json()
    old_id = next(r["id"] for r in rows if r["label"] == "old phone")

    assert client.delete(f"/api/keys/{old_id}", headers={"X-API-Key": mine}).status_code == 200
    assert client.get("/api/me", headers={"X-API-Key": old}).status_code == 401
    assert client.get("/api/me", headers={"X-API-Key": mine}).status_code == 200
    rows = client.get("/api/keys", headers={"X-API-Key": mine}).json()
    assert next(r for r in rows if r["id"] == old_id)["revoked_at"] is not None


def test_cannot_revoke_the_key_in_use(client, make_key):
    mine = make_key("brandan", "laptop")
    my_id = client.get("/api/keys", headers={"X-API-Key": mine}).json()[0]["id"]
    assert client.delete(f"/api/keys/{my_id}", headers={"X-API-Key": mine}).status_code == 409
    assert client.get("/api/me", headers={"X-API-Key": mine}).status_code == 200


def test_cannot_revoke_someone_elses_key(client, make_key):
    mine = make_key("brandan")
    sam = make_key("sam")
    sam_id = client.get("/api/keys", headers={"X-API-Key": sam}).json()[0]["id"]
    assert client.delete(f"/api/keys/{sam_id}", headers={"X-API-Key": mine}).status_code == 404
    assert client.get("/api/me", headers={"X-API-Key": sam}).status_code == 200


def test_keys_need_a_valid_key(client):
    assert client.get("/api/keys").status_code == 401
    assert client.delete("/api/keys/1").status_code == 401
