import datetime as dt

import pytest

from app import labels

TODAY = dt.date(2026, 11, 10)


@pytest.mark.parametrize(
    "period,expected",
    [
        (None, ""),
        ("day-2026-11-04", "Nov 4"),
        ("day-2026-11-14", "Nov 14"),
        ("wk-2026-11-02", "Week of Nov 2"),
        ("2026-10", "October 2026"),
        ("something-odd", "something-odd"),
        ("day-2026-13-45", "day-2026-13-45"),
    ],
)
def test_period_text(period, expected):
    assert labels.period_text(period, TODAY) == expected


def test_year_is_added_only_when_it_is_not_the_current_year():
    assert labels.period_text("day-2025-11-04", TODAY) == "Nov 4, 2025"
    assert labels.period_text("wk-2025-12-29", TODAY) == "Week of Dec 29, 2025"
    assert labels.period_text("day-2026-11-04", dt.date(2027, 1, 3)) == "Nov 4, 2026"
    assert labels.period_text("2025-10", TODAY) == "October 2025"  # months always carry the year


def what(category, period=None, note=None, type_="earn", reward=None):
    return labels.entry_what(type_, category, period, note, reward, TODAY)


def test_daily_rows():
    assert what("calorie_daily", "day-2026-11-04") == "Daily Calorie Goal Reached \u00b7 Nov 4"
    assert what("workout_day", "day-2026-11-04") == "Complete Workout Finished \u00b7 Nov 4"


def test_weekly_rows():
    assert what("calorie_weekly", "wk-2026-11-02", "7 of 7 Days") == (
        "Weekly Calorie Goal Hit: 7 of 7 Days \u00b7 Week of Nov 2")
    assert what("calorie_weekly", "wk-2026-11-02", "6 of 7 Days").startswith("Weekly Calorie Goal Hit: 6 of 7 Days")
    assert what("workout_goal", "wk-2026-11-02") == "Weekly Workout Goal Reached \u00b7 Week of Nov 2"
    assert what("team_bonus", "wk-2026-11-02").startswith("Team Bonus: Everyone Succeeded This Week")


def test_streak_and_monthly_rows():
    assert what("multiplier", "wk-2026-11-09", "x1.1 (2 Weeks in a Row)") == (
        "Streak Bonus x1.1 (2 Weeks in a Row) \u00b7 Week of Nov 9")
    assert what("calorie_monthly", "2026-10", "27 of 30 Days (90%)") == (
        "Monthly Calorie Consistency: 27 of 30 Days (90%) \u00b7 October 2026")


def test_rows_without_a_note_still_read_fine():
    assert what("calorie_weekly", "wk-2026-11-02") == "Weekly Calorie Goal Hit \u00b7 Week of Nov 2"
    assert what("multiplier", "wk-2026-11-09") == "Streak Bonus \u00b7 Week of Nov 9"


def test_redeem_and_adjust_rows():
    assert what("redeem", type_="redeem", reward="Movie Night") == "Redeemed: Movie Night"
    assert what("adjust", type_="adjust", note="Double counted") == "Adjustment: Double counted"


def test_unknown_category_falls_back_to_words():
    assert what("some_new_thing") == "Some New Thing"


def test_late_update_rows():
    assert what("calorie_daily", "wk-2026-11-02", "Late Update") == (
        "Daily Calorie Goal Reached: Late Update \u00b7 Week of Nov 2")
