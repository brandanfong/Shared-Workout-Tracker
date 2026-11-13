"""The plain-English text shown in the points history ("What" column).

Kept in one place so the table and the search use the same words. Nothing here
touches the database: scoring writes a category, a period and (for a few rows) a
short note, and this turns them into a sentence."""

import datetime as dt

from app import config

# What each kind of earned row is called.
CATEGORY_LABELS = {
    "calorie_daily": "Daily Calorie Goal Reached",
    "workout_day": "Complete Workout Finished",
    "calorie_weekly": "Weekly Calorie Goal Hit",
    "workout_goal": "Weekly Workout Goal Reached",
    "team_bonus": "Team Bonus: Everyone Succeeded This Week",
    "multiplier": "Streak Bonus",
    "calorie_monthly": "Monthly Calorie Consistency",
}

# How a row's note is attached to its label, where it has one worth showing:
#   Weekly Calorie Goal Hit: 7 of 7 Days
#   Streak Bonus x1.1 (2 Weeks in a Row)
NOTE_JOINER = {
    "calorie_daily": ": ",
    "workout_day": ": ",
    "calorie_weekly": ": ",
    "calorie_monthly": ": ",
    "multiplier": " ",
}


def _short_date(d: dt.date, today: dt.date) -> str:
    """'Nov 4' this year, 'Nov 4, 2025' in any other year."""
    return f"{d:%b} {d.day}" if d.year == today.year else f"{d:%b} {d.day}, {d.year}"


def period_text(period: str | None, today: dt.date | None = None) -> str:
    """'day-2026-11-04' -> 'Nov 4', 'wk-2026-11-02' -> 'Week of Nov 2', '2026-10' ->
    'October 2026'. The year is added to days and weeks that are not in the current year."""
    if not period:
        return ""
    today = today or config.today()
    try:
        if period.startswith("day-"):
            return _short_date(dt.date.fromisoformat(period[4:]), today)
        if period.startswith("wk-"):
            return f"Week of {_short_date(dt.date.fromisoformat(period[3:]), today)}"
        if len(period) == 7 and period[4] == "-":
            return f"{dt.date(int(period[:4]), int(period[5:]), 1):%B %Y}"
    except ValueError:
        pass
    return period


def entry_what(
    type_: str,
    category: str,
    period: str | None,
    note: str | None,
    reward: str | None,
    today: dt.date | None = None,
) -> str:
    """The sentence for one ledger row."""
    if type_ == "redeem":
        detail = reward or note
        return f"Redeemed: {detail}" if detail else "Redeemed"
    if type_ == "adjust":
        return f"Adjustment: {note}" if note else "Adjustment"
    text = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
    if note and category in NOTE_JOINER:
        text += NOTE_JOINER[category] + note
    when = period_text(period, today)
    return f"{text} · {when}" if when else text
