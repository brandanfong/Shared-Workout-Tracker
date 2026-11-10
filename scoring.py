"""Pure scoring rules (no database). See the scoring doc: the numbers here are
the single place to tune them.

All percentage checks use integer math (calories * 100 >= goal * tier) so there
are no float rounding surprises."""

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CALORIE_TIERS = [(200, 3), (150, 2), (100, 1)]  # (pct of goal, points), highest first
WEEKLY_HIT_BONUS = {7: 5, 6: 3}  # calorie days hit -> points
PER_WORKOUT_DAY = 2
GOAL_BONUS = 4
SUCCESS_MIN_CALORIE_DAYS = 5
TEAM_BONUS = 4
MULTIPLIERS = [(8, Decimal("1.5")), (4, Decimal("1.25")), (2, Decimal("1.1"))]  # streak >= n
MONTHLY_TIERS = [(100, 20), (90, 10), (80, 5)]  # pct of days in month hit -> points

# A week is scored the Tuesday after it ends (one day of grace for the Monday sync).
GRACE_DAYS = 1
# ...but only once every player has synced that Sunday, or after this many days.
READY_TIMEOUT_DAYS = 7


@dataclass
class Goals:
    min_minutes: int
    min_calories: int
    weekly_workouts: int


@dataclass
class PlayerWeek:
    calorie_daily: int = 0
    calorie_weekly: int = 0
    workout_day: int = 0
    workout_goal: int = 0
    calorie_days_hit: int = 0
    workout_days: int = 0
    ok: bool = False
    components: dict = field(default_factory=dict)

    @property
    def subtotal(self) -> int:
        return self.calorie_daily + self.calorie_weekly + self.workout_day + self.workout_goal


def calorie_day_points(calories: int, goal: int) -> int:
    for pct, pts in CALORIE_TIERS:
        if calories * 100 >= goal * pct:
            return pts
    return 0


def is_workout_day(minutes: int, calories: int, goals: Goals) -> bool:
    # Same rule as the calendar: BOTH thresholds must be met.
    return minutes >= goals.min_minutes and calories >= goals.min_calories


def score_day(minutes: int, calories: int, goals: Goals) -> tuple[int, int]:
    """One day's points: (calorie tier points, workout-day points)."""
    return (
        calorie_day_points(calories, goals.min_calories),
        PER_WORKOUT_DAY if is_workout_day(minutes, calories, goals) else 0,
    )


def week_days(monday: dt.date) -> list[dt.date]:
    return [monday + dt.timedelta(days=i) for i in range(7)]


def score_player_week(days: dict[dt.date, tuple[int, int]], monday: dt.date, goals) -> PlayerWeek:
    """days maps date -> (exercise_minutes, active_calories). Missing day = 0.

    `goals` is either one Goals (used for every day) or a function day -> Goals.
    Minutes and calories are judged against the goals in force on each day. The
    weekly workout target is the one in force on the week's Sunday: change it any
    time during the week and it counts for that whole week; it locks when the
    week ends (a save on the following Monday belongs to the next week)."""
    goals_on = goals if callable(goals) else (lambda _d: goals)
    w = PlayerWeek()
    for d in week_days(monday):
        minutes, calories = days.get(d, (0, 0))
        g = goals_on(d)
        pts = calorie_day_points(calories, g.min_calories)
        w.calorie_daily += pts
        if pts > 0:  # any tier means >= 100% of goal
            w.calorie_days_hit += 1
        if is_workout_day(minutes, calories, g):
            w.workout_days += 1
    w.calorie_weekly = WEEKLY_HIT_BONUS.get(w.calorie_days_hit, 0)
    w.workout_day = w.workout_days * PER_WORKOUT_DAY
    reached_goal = w.workout_days >= goals_on(monday + dt.timedelta(days=6)).weekly_workouts
    w.workout_goal = GOAL_BONUS if reached_goal else 0
    w.ok = w.calorie_days_hit >= SUCCESS_MIN_CALORIE_DAYS and reached_goal
    return w


def multiplier_for(streak: int) -> Decimal:
    for n, m in MULTIPLIERS:
        if streak >= n:
            return m
    return Decimal("1.0")


def scaled(points: int, mult: Decimal) -> int:
    """Multiply once, round half up to a whole number."""
    return int((Decimal(points) * mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def monthly_points(days_hit: int, days_in_month: int) -> int:
    for pct, pts in MONTHLY_TIERS:
        if days_hit * 100 >= days_in_month * pct:
            return pts
    return 0


def week_ready_date(monday: dt.date) -> dt.date:
    """First day the week may be scored (the Tuesday after it ends)."""
    return monday + dt.timedelta(days=6 + 1 + GRACE_DAYS)


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    y, m = int(month[:4]), int(month[5:])
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), (m % 12) + 1, 1)
    return first, nxt - dt.timedelta(days=1)
