"""Startup database setup: create tables, small in-place migrations, and the
starter rewards. Everything here is safe to run on every startup."""
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app import colors, config
from app.db import Base, engine
from app.models import Reward, User

STARTER_REWARDS = [
    # (name, cost, cooldown_days)
    ("Coffee or dessert date", 50, 0),
    ("Movie night, winner picks", 75, 0),
    ("Home-cooked dinner by the other", 100, 0),
    ("$25 gift", 200, 7),
    ("Nice dinner out", 325, 14),
    ("Spa day or day trip", 500, 30),
    ("Weekend getaway fund", 1000, 60),
]


def migrate_user_color() -> None:
    """create_all does not add columns to existing tables."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS color VARCHAR"))


def migrate_auto_color() -> None:
    """Add users.auto_color and give existing people the automatic color they
    already see (their place in the alphabetical list), so nothing changes
    for them. Safe to run on every startup: only fills people who have none."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_color VARCHAR"))
    with Session(engine) as db:
        users = db.scalars(select(User).order_by(User.name)).all()
        palette = colors.DEFAULT_PALETTE
        changed = False
        for i, u in enumerate(users):
            if u.auto_color is None:
                u.auto_color = palette[i % len(palette)]
                changed = True
        if changed:
            db.commit()


def migrate_user_settings() -> None:
    """One-time: copy the old single-row-per-person user_settings into the goal
    history as their starting version. Safe to run on every startup. The old
    table is left in place, unused."""
    insp = inspect(engine)
    if not insp.has_table("user_settings"):
        return
    cols = {c["name"] for c in insp.get_columns("user_settings")}
    weekly = "s.weekly_workouts" if "weekly_workouts" in cols else str(config.DEFAULT_WEEKLY_WORKOUTS)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_goals (user_id, effective_from, min_minutes, min_calories, weekly_workouts) "
                f"SELECT s.user_id, DATE '1970-01-01', s.min_minutes, s.min_calories, {weekly} "
                "FROM user_settings s "
                "WHERE NOT EXISTS (SELECT 1 FROM user_goals g WHERE g.user_id = s.user_id)"
            )
        )


def seed_rewards() -> None:
    """Fill the menu with the starter rewards, only if it has never had any."""
    with Session(engine) as db:
        if db.scalar(select(func.count()).select_from(Reward)):
            return
        db.add_all(
            Reward(name=n, cost=c, cooldown_days=cd, repeatable=True)
            for n, c, cd in STARTER_REWARDS
        )
        db.commit()


def setup_database() -> None:
    Base.metadata.create_all(engine)  # swap for Alembic migrations later
    migrate_user_color()
    migrate_auto_color()
    migrate_user_settings()
    seed_rewards()
