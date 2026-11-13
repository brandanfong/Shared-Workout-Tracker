import datetime as dt

from sqlalchemy import text

from app import config
from app.db import engine


def test_today_follows_pacific_not_utc(monkeypatch):
    # 2026-11-13 02:00 UTC is still the evening of Nov 12 in Pacific (UTC-8).
    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 13, 2, 0, tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(config.dt, "datetime", FakeDT)
    assert config.today() == dt.date(2026, 11, 12)


def test_history_chart_buckets_days_in_pacific(client, auth, make_key):
    # An entry made at 02:00 UTC on Nov 13 is 6pm on Nov 12 in Pacific.
    client.get("/api/me", headers=auth)
    with engine.begin() as conn:
        uid = conn.execute(text("SELECT id FROM users")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ledger (type, user_id, category, points, balance_after, created_at) "
                "VALUES ('earn', :u, 'workout_day', 10, 10, '2026-11-13 02:00:00+00')"
            ),
            {"u": uid},
        )
    data = client.get("/api/points-history", headers=auth).json()
    assert data["earned"][0]["date"] == "2026-11-12"
    assert data["balance"][0]["date"] == "2026-11-12"
