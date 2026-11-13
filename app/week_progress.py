"""Live progress for the current week (the "This week" panel).

Nothing here is paid or stored: it is a read-only preview built from the same
rules as scoring, so the panel and the real week-close agree. Today's numbers
may be partial (the phone only syncs when it is unlocked), so a goal that
today has not hit yet is still counted as reachable today."""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, scoring
from app.goals import GoalBook, load_goal_book
from app.models import DailyActivity, ScoringPeriod, User


def pace(done: int, target: int, left: int, elapsed: int, span: int) -> str:
    """Where one goal stands.

    done     days that already count
    target   days needed
    left     days still able to count (today, if not hit yet, and the rest of the week)
    elapsed  judged days already over (before today)
    span     judged days in the whole week (7, fewer in a grace week)"""
    need = target - done
    if need <= 0:
        return "done"
    if need > left:
        return "out_of_reach"
    if need == 1:
        return "almost"
    if need == left:
        return "no_slack"
    # On pace if done keeps up with an even spread of the target over the week.
    if done * span >= target * elapsed:
        return "on_pace"
    return "behind"


def _goal(done: int, target: int, left: int, elapsed: int, span: int) -> dict:
    return {
        "done": done,
        "target": target,
        "left": left,
        "status": pace(done, target, left, elapsed, span),
    }


def person_week(
    days: dict[dt.date, tuple[int, int]],
    book: GoalBook,
    user_id: int,
    monday: dt.date,
    today: dt.date,
) -> dict:
    """One person's week so far. `days` maps date -> (minutes, calories)."""
    sunday = monday + dt.timedelta(days=6)
    first = book.first_day(user_id)
    start = max(monday, first)
    judged = [d for d in scoring.week_days(monday) if d >= start]
    elapsed = sum(1 for d in judged if d < today)
    remaining = [d for d in judged if d >= today]

    cal_hit = workouts = 0
    cal_today = workout_today = False
    for d in judged:
        if d > today:
            break
        minutes, calories = days.get(d, (0, 0))
        g = book.at(user_id, d)
        hit = scoring.calorie_day_points(calories, g.min_calories) > 0
        worked = scoring.is_workout_day(minutes, calories, g)
        cal_hit += hit
        workouts += worked
        if d == today:
            cal_today, workout_today = hit, worked

    # The weekly target is the one in force on Sunday; until then, today's.
    target = book.at(user_id, min(today, sunday)).weekly_workouts
    span = len(judged)
    calories = _goal(cal_hit, scoring.SUCCESS_MIN_CALORIE_DAYS, len(remaining) - cal_today, elapsed, span)
    calories["bonus"] = scoring.WEEKLY_HIT_BONUS.get(cal_hit, 0)
    workout = _goal(workouts, target, len(remaining) - workout_today, elapsed, span)

    statuses = {calories["status"], workout["status"]}
    if statuses == {"done"}:
        overall = "qualified"
    elif "out_of_reach" in statuses:
        overall = "out"
    elif statuses & {"behind", "no_slack"}:
        overall = "behind"
    else:
        overall = "on_track"
    return {
        # Joined after Monday: earns points but is not in the team check this week.
        "grace": first > monday,
        "calories": calories,
        "workouts": workout,
        "overall": overall,
    }


def week_progress(db: Session, user: User, today: dt.date | None = None) -> dict:
    """Only `user`'s own progress: nothing about teammates is returned, so nobody
    can see (and be discouraged by) someone else's week."""
    today = today or config.today()
    monday = today - dt.timedelta(days=today.weekday())
    sunday = monday + dt.timedelta(days=6)
    book = load_goal_book(db)

    me = None
    if book.latest(user.id) is not None:
        days = {
            r.date: (r.exercise_minutes, r.active_calories)
            for r in db.execute(
                select(
                    DailyActivity.date,
                    DailyActivity.exercise_minutes,
                    DailyActivity.active_calories,
                ).where(
                    DailyActivity.user_id == user.id,
                    DailyActivity.date >= monday,
                    DailyActivity.date <= sunday,
                )
            )
        }
        me = person_week(days, book, user.id, monday, today)

    out = {
        "monday": monday.isoformat(),
        "sunday": sunday.isoformat(),
        "today": today.isoformat(),
        "days_left": (sunday - today).days + 1,
        "me": me,
        "streak": None,
    }
    if config.SCORING_ENABLED:
        last = db.scalars(
            select(ScoringPeriod)
            .where(ScoringPeriod.kind == "week")
            .order_by(ScoringPeriod.period.desc())
            .limit(1)
        ).first()
        streak = (last.streak or 0) if last else 0
        # Only promise a next multiplier when last week is already settled;
        # otherwise the streak this week builds on is not known yet.
        settled = last is not None and last.period == f"wk-{(monday - dt.timedelta(days=7)).isoformat()}"
        out["streak"] = {
            "weeks": streak,
            "multiplier": str(scoring.multiplier_for(streak)),
            "last_week_settled": settled,
            "next_multiplier": str(scoring.multiplier_for(streak + 1)) if settled else None,
        }
    return out
