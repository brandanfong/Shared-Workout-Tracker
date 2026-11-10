"""Fill the TEST database with fake people and activity so the charts, calendar
and rewards pages have something to show.

    python seed_demo.py

Reads TEST_DATABASE_URL from .env. It WIPES every table in that database first
(the same thing the test suite does), and refuses to run if it points at the
same database as DATABASE_URL, so your real data is never touched.

At the end it prints the demo API keys and the environment to use when starting
the server against the demo data.
"""
import datetime as dt
import os
import random
import re
import sys
from pathlib import Path


def read_env() -> dict[str, str]:
    env = {}
    path = Path(__file__).parent / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def norm(url: str | None) -> str:
    return (url or "").strip().strip("'\"").replace("+psycopg", "", 1)


env = read_env()
test_url = os.environ.get("TEST_DATABASE_URL") or env.get("TEST_DATABASE_URL")
real_url = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL")
if not test_url:
    sys.exit("TEST_DATABASE_URL is not set (put it in .env).")
if norm(test_url) == norm(real_url):
    sys.exit("TEST_DATABASE_URL is the same database as DATABASE_URL. Refusing to wipe it.")

WEEKS = 8
today = dt.date.today()
this_monday = today - dt.timedelta(days=today.weekday())
start = this_monday - dt.timedelta(weeks=WEEKS)

# Must be set BEFORE main is imported (it reads these at import time).
os.environ["DATABASE_URL"] = test_url
os.environ["SCORING_ENABLED"] = "true"
os.environ["SCORING_START_DATE"] = start.isoformat()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import main  # noqa: E402

PEOPLE = {
    # name: (calorie goal, minutes goal, workouts a week, typical workout cal, rest-day cal)
    "demo_a": (500, 30, 4, 720, 430),
    "demo_b": (400, 30, 3, 560, 330),
    "demo_c": (450, 40, 5, 650, 380),
}

rng = random.Random(7)
print(f"Wiping and seeding host {main.engine.url.host} ...")
with main.engine.begin() as conn:
    conn.execute(
        text(
            "TRUNCATE scoring_periods, ledger, rewards, daily_activity, api_keys, "
            "user_goals, users RESTART IDENTITY CASCADE"
        )
    )
main.seed_rewards()

keys = {}
with Session(main.engine) as db:
    for name, (cal_goal, min_goal, per_week, workout_cal, rest_cal) in PEOPLE.items():
        keys[name] = main.issue_key(db, name, "demo")
        user = db.scalar(main.select(main.User).where(main.User.name == name))
        db.add(
            main.UserGoal(
                user_id=user.id,
                effective_from=main.EPOCH,
                min_minutes=min_goal,
                min_calories=cal_goal,
                weekly_workouts=per_week,
            )
        )
        day = start
        while day < today:  # up to yesterday; today is still in progress
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
                main.DailyActivity(
                    user_id=user.id, date=day, exercise_minutes=minutes, active_calories=calories
                )
            )
            day += dt.timedelta(days=1)
    db.commit()

    scored = main.run_scoring(db, today)
    balance = main.current_balance(db)

print(f"Scored {len(scored)} periods; shared balance is {balance} points.\n")
print("Demo API keys (for the throwaway test database only):")
for name, key in keys.items():
    print(f"  {name}: {key}")
print(
    "\nStart the server against the demo data in a PowerShell window:\n"
    "  Get-Content .env | % { if ($_ -match '^\\s*TEST_DATABASE_URL\\s*=\\s*(.*)$') "
    "{ $env:DATABASE_URL = $Matches[1].Trim() } }\n"
    f"  $env:SCORING_ENABLED = 'true'; $env:SCORING_START_DATE = '{start.isoformat()}'\n"
    "  .\\venv\\Scripts\\python.exe -m uvicorn main:app --reload"
)
