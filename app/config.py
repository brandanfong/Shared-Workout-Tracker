"""Settings read from the environment.

Other modules read these as `config.NAME` / `config.today()` (not
`from app.config import NAME`) so tests can monkeypatch them in one place.
"""
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Tolerate stray quotes/whitespace and a plain postgresql:// prefix
DATABASE_URL = os.environ["DATABASE_URL"].strip().strip("'\"")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Default goals for anyone who hasn't set their own. A "workout day" needs BOTH
# thresholds met. Override the defaults via env vars, no code edit.
DEFAULT_MIN_MINUTES = int(os.getenv("MIN_WORKOUT_MINUTES", "30"))
DEFAULT_MIN_CALORIES = int(os.getenv("MIN_WORKOUT_CALORIES", "400"))
DEFAULT_WEEKLY_WORKOUTS = int(os.getenv("WEEKLY_WORKOUT_GOAL", "2"))
# Nothing before this date is ever scored (backfilled history is ignored).
SCORING_START = dt.date.fromisoformat(os.getenv("SCORING_START_DATE", "2026-11-09"))
# Master switch. While off, nothing is scored and no points are paid; data keeps
# being stored. Turn on with SCORING_ENABLED=true once everyone has set goals.
SCORING_ENABLED = os.getenv("SCORING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


# The group's calendar. "Today", the scoring days and the history chart's days all
# follow this one zone, not the server's clock (UTC on Render). Per-user zones later.
GROUP_TIMEZONE = os.getenv("GROUP_TIMEZONE", "America/Los_Angeles")


def today() -> dt.date:
    return dt.datetime.now(ZoneInfo(GROUP_TIMEZONE)).date()
