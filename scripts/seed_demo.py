"""Fill a DEMO database with fake people and activity so the charts, calendar
and rewards pages have something to show.

    python scripts/seed_demo.py

Reads DEMO_DATABASE_URL from .env (a separate database, such as a Neon branch
called "demo", so running the tests never wipes it). If that is not set it falls
back to TEST_DATABASE_URL. It WIPES every table in that database first, and
refuses to run if it points at the same database as DATABASE_URL, so your real
data is never touched.

At the end it prints the demo API keys and the environment to use when starting
the server against the demo data.
"""
import datetime as dt
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `import app` works when run as a script

from app.envfile import read_env, same_db  # noqa: E402

env = read_env()
DEMO_VAR = "DEMO_DATABASE_URL"
demo_url = os.environ.get(DEMO_VAR) or env.get(DEMO_VAR)
if not demo_url:
    DEMO_VAR = "TEST_DATABASE_URL"  # fallback: shared with the test suite, which wipes it
    demo_url = os.environ.get(DEMO_VAR) or env.get(DEMO_VAR)
if not demo_url:
    sys.exit("DEMO_DATABASE_URL is not set (put it in .env).")
# Check against the real URL in .env AND anything set in this shell.
if same_db(demo_url, env.get("DATABASE_URL")) or same_db(demo_url, os.environ.get("DATABASE_URL")):
    sys.exit(f"{DEMO_VAR} is the same database as DATABASE_URL. Refusing to wipe it.")
test_url = os.environ.get("TEST_DATABASE_URL") or env.get("TEST_DATABASE_URL")
if DEMO_VAR == "DEMO_DATABASE_URL" and same_db(demo_url, test_url):
    print("Note: DEMO_DATABASE_URL is the same database as TEST_DATABASE_URL, so running "
          "the tests will wipe the demo data. Use a separate branch to avoid that.\n")

WEEKS = 8
today = dt.date.today()
this_monday = today - dt.timedelta(days=today.weekday())
start = this_monday - dt.timedelta(weeks=WEEKS)

# Must be set BEFORE the app is imported (it reads these at import time).
os.environ["DATABASE_URL"] = demo_url
os.environ["SCORING_ENABLED"] = "true"
os.environ["SCORING_START_DATE"] = start.isoformat()

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import hash_key  # noqa: E402
from app.db import engine  # noqa: E402
from app.ledger import current_balance  # noqa: E402
from app.setup_db import seed_rewards, setup_database  # noqa: E402
from app.models import EPOCH, ApiKey, DailyActivity, LedgerEntry, Reward, User, UserGoal  # noqa: E402
from app.scoring_runner import run_scoring  # noqa: E402

PEOPLE = {
    # name: (calorie goal, minutes goal, workouts a week, typical workout cal, rest-day cal)
    "demo_a": (500, 30, 4, 720, 430),
    "demo_b": (400, 30, 3, 560, 330),
    "demo_c": (450, 40, 5, 650, 380),
    "demo_d": (550, 30, 3, 800, 470),
    "demo_e": (350, 25, 4, 500, 290),
    "demo_f": (480, 45, 2, 700, 410),
}

def spread_dates(db) -> None:
    """run_scoring stamps every entry with "now", which would put all the points on
    one day in the history graphs. Give each earn entry the date it would really
    have landed, add a few redemptions so the shared balance dips, then redo the
    running balance in date order."""
    utc = dt.timezone.utc
    now = dt.datetime.now(utc)

    def landed(period: str) -> dt.datetime:
        if period.startswith("day-"):  # paid two days after the day
            d = dt.date.fromisoformat(period[4:]) + dt.timedelta(days=2)
        elif period.startswith("wk-"):  # scored the Tuesday after the week
            d = dt.date.fromisoformat(period[3:]) + dt.timedelta(days=8)
        else:  # "YYYY-MM": the monthly bonus, paid just after the month closes
            y, m = map(int, period.split("-"))
            d = dt.date(y + (m == 12), m % 12 + 1, 3)
        return min(dt.datetime.combine(d, dt.time(12, 0), tzinfo=utc), now)

    for e in db.scalars(select(LedgerEntry)).all():
        if e.period:
            e.created_at = landed(e.period)
    db.flush()

    rewards = db.scalars(select(Reward).order_by(Reward.cost)).all()
    users = {u.name: u.id for u in db.scalars(select(User)).all()}
    for days_ago, who in ((35, "demo_a"), (21, "demo_c"), (9, "demo_b")):
        when = dt.datetime.combine(today - dt.timedelta(days=days_ago), dt.time(18, 0), tzinfo=utc)
        bal = sum(db.scalars(select(LedgerEntry.points).where(LedgerEntry.created_at <= when)).all())
        affordable = [r for r in rewards if r.cost <= bal * 0.6]
        if not affordable:
            continue
        r = affordable[-1]
        db.add(LedgerEntry(
            type="redeem", user_id=users[who], category="redeem", points=-r.cost,
            balance_after=0, reward_id=r.id, note=r.name, created_at=when,
        ))
        db.flush()

    running = 0
    for e in db.scalars(
        select(LedgerEntry).order_by(LedgerEntry.created_at, LedgerEntry.id)
    ).all():
        running += e.points
        e.balance_after = running
    db.commit()


rng = random.Random(7)
setup_database()  # create tables if this is a fresh database
print(f"Wiping and seeding host {engine.url.host} ...")
with engine.begin() as conn:
    conn.execute(
        text(
            "TRUNCATE scoring_periods, ledger, rewards, daily_activity, api_keys, "
            "user_goals, users RESTART IDENTITY CASCADE"
        )
    )
seed_rewards()

keys = {}
with Session(engine) as db:
    for name, (cal_goal, min_goal, per_week, workout_cal, rest_cal) in PEOPLE.items():
        # Fixed, easy-to-remember keys (demo_a -> "demo-a"), so nothing needs looking up
        # after a re-seed. They only work against this throwaway demo database.
        user = User(name=name)
        db.add(user)
        db.flush()
        keys[name] = name.replace("_", "-")
        db.add(ApiKey(user_id=user.id, key_hash=hash_key(keys[name]), label="demo (fixed key)"))
        db.add(
            UserGoal(
                user_id=user.id,
                effective_from=EPOCH,
                min_minutes=min_goal,
                min_calories=cal_goal,
                weekly_workouts=per_week,
            )
        )
        day = start
        while day <= today:  # through today, as if everyone synced this morning
            if day.weekday() == 0 or day == start:
                workout_days = set(rng.sample(range(7), per_week + rng.choice([-1, 0, 0, 1])))
            if rng.random() < 0.06:  # the odd missed sync / day off the grid
                day += dt.timedelta(days=1)
                continue
            if day.weekday() in workout_days:
                minutes = rng.randint(30, 75)
                calories = int(workout_cal * rng.uniform(0.85, 1.35))
            else:
                minutes = rng.choice([0, 0, 5, 12, 20])
                calories = int(rest_cal * rng.uniform(0.75, 1.3))
            db.add(
                DailyActivity(
                    user_id=user.id, date=day, exercise_minutes=minutes, active_calories=calories
                )
            )
            day += dt.timedelta(days=1)
    db.commit()

    scored = run_scoring(db, today)
    spread_dates(db)
    balance = current_balance(db)

print(f"Scored {len(scored)} periods; shared balance is {balance} points.\n")
print("Demo API keys (fixed; they only work against the demo database):")
for name, key in keys.items():
    print(f"  {name}: {key}")
print(
    "\nStart the server against the demo data:\n"
    "  .\\venv\\Scripts\\python.exe -m app --demo"
)
