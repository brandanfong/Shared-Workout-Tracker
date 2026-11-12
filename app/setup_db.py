"""Bring the database up to date on startup: apply any new Alembic migrations
(files in alembic/versions/), then add the starter rewards if the menu is empty.
Safe to run on every startup."""
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.db import Base, engine
from app.envfile import ROOT
from app.models import Reward

# The first migration. Databases made before Alembic are marked as being at it.
INITIAL_REVISION = "0001"

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


def upgrade_database() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        insp = inspect(conn)
        if insp.has_table("users") and not insp.has_table("alembic_version"):
            # One-time handover for a database created before Alembic (the real
            # and demo databases): create any missing tables, then record it as
            # being at the first migration. Can be deleted once both have run it.
            Base.metadata.create_all(conn)
            command.stamp(cfg, INITIAL_REVISION)
        command.upgrade(cfg, "head")


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
    upgrade_database()
    seed_rewards()
