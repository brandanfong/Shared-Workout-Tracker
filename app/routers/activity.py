"""Logging activity from the phone, and reading it back for charts and the calendar."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import colors, config, scoring
from app.auth import current_user
from app.db import get_session
from app.goals import effective_goals, load_goal_book
from app.models import DailyActivity, User
from app.schemas import LogIn
from app.scoring_runner import run_scoring
from app.users import effective_colors

router = APIRouter()

MAX_RANGE_DAYS = 730


@router.post("/log")
def log(
    body: LogIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    stmt = insert(DailyActivity).values(
        user_id=user.id,
        date=body.date,
        exercise_minutes=body.minutes,
        active_calories=body.calories,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "date"],
        set_={
            # keep the larger value so a bad run can't lower a day's totals
            "exercise_minutes": func.greatest(
                DailyActivity.exercise_minutes, stmt.excluded.exercise_minutes
            ),
            "active_calories": func.greatest(
                DailyActivity.active_calories, stmt.excluded.active_calories
            ),
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()
    try:  # score any week/month that has just become ready; never fail the sync
        run_scoring(db)
    except Exception:
        db.rollback()
    return {"status": "ok", "name": user.name, "date": body.date}


@router.get("/api/activity", dependencies=[Depends(current_user)])
def activity(
    days: int = Query(30, ge=1, le=365),
    start: dt.date | None = None,
    end: dt.date | None = None,
    db: Session = Depends(get_session),
):
    """Daily activity for the charts. Either the last `days` days, or a custom
    range with `start` and/or `end` (both inclusive; `end` defaults to today and
    `start` to `days` before it)."""
    today = config.today()
    if start is None and end is None:
        first, last = today - dt.timedelta(days=days), None
    else:
        last = end or today
        first = start or last - dt.timedelta(days=days)
        if first > last:
            raise HTTPException(status_code=422, detail="The start date must be on or before the end date.")
        if (last - first).days > MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=422, detail=f"Pick a range of {MAX_RANGE_DAYS} days or fewer."
            )
    stmt = (
        select(
            User.name,
            DailyActivity.date,
            DailyActivity.exercise_minutes,
            DailyActivity.active_calories,
        )
        .join(User, User.id == DailyActivity.user_id)
        .where(DailyActivity.date >= first)
        .order_by(DailyActivity.date, User.name)
    )
    if last is not None:
        stmt = stmt.where(DailyActivity.date <= last)
    return [
        {
            "name": r.name,
            "date": r.date.isoformat(),
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
        }
        for r in db.execute(stmt).all()
    ]


@router.get("/api/calendar", dependencies=[Depends(current_user)])
def calendar_month(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_session),
):
    year, mon = int(month[:4]), int(month[5:])
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    start = dt.date(year, mon, 1)
    end = dt.date(year + (mon == 12), (mon % 12) + 1, 1)

    users = db.scalars(select(User.name).order_by(User.name)).all()
    rows = db.execute(
        select(
            User.id.label("uid"),
            User.name,
            DailyActivity.date,
            DailyActivity.exercise_minutes,
            DailyActivity.active_calories,
        )
        .join(User, User.id == DailyActivity.user_id)
        .where(DailyActivity.date >= start, DailyActivity.date < end)
    ).all()

    goals = effective_goals(db)  # shown as the current goals
    book = load_goal_book(db)
    days: dict[str, dict] = {}
    for r in rows:
        # Each day is judged against the goals that applied on that day.
        has_goals = book.judged(r.uid, r.date)
        g = book.at(r.uid, r.date)
        days.setdefault(r.date.isoformat(), {})[r.name] = {
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
            # Nothing to judge against until they set goals.
            "goals_set": has_goals,
            "workout": has_goals and scoring.is_workout_day(r.exercise_minutes, r.active_calories, g),
            # The two halves of a workout day, so the calendar can show partial days.
            "calorie_hit": has_goals and r.active_calories >= g.min_calories,
            "minutes_hit": has_goals and r.exercise_minutes >= g.min_minutes,
        }

    return {
        "month": month,
        "users": users,
        "goals": goals,
        "colors": effective_colors(db),
        "everyone_color": colors.EVERYONE_COLOR,
        "days": days,
    }
