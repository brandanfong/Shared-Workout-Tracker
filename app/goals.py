"""Everyone's workout goals over time."""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, scoring
from app.models import User, UserGoal

DEFAULT_GOALS = scoring.Goals(
    config.DEFAULT_MIN_MINUTES, config.DEFAULT_MIN_CALORIES, config.DEFAULT_WEEKLY_WORKOUTS
)


def goal_dict(g: scoring.Goals) -> dict:
    return {
        "min_minutes": g.min_minutes,
        "min_calories": g.min_calories,
        "weekly_workouts": g.weekly_workouts,
    }


class GoalBook:
    """Everyone's goals over time. at(user_id, day) is the version in force on
    that day (the defaults if the person has none yet)."""

    def __init__(self, rows: list[UserGoal]):
        self._by_user: dict[int, list[tuple[dt.date, scoring.Goals]]] = {}
        for r in sorted(rows, key=lambda r: r.effective_from):
            self._by_user.setdefault(r.user_id, []).append(
                (r.effective_from, scoring.Goals(r.min_minutes, r.min_calories, r.weekly_workouts))
            )

    def at(self, user_id: int, day: dt.date) -> scoring.Goals:
        current = DEFAULT_GOALS
        for effective_from, goals in self._by_user.get(user_id, []):
            if effective_from > day:
                break
            current = goals
        return current

    def latest(self, user_id: int):
        rows = self._by_user.get(user_id)
        return rows[-1][1] if rows else None

    def first_day(self, user_id: int) -> dt.date | None:
        """The day their goals first took effect. Days before it are not judged
        or scored. None if they have not set goals."""
        rows = self._by_user.get(user_id)
        return rows[0][0] if rows else None

    def judged(self, user_id: int, day: dt.date) -> bool:
        first = self.first_day(user_id)
        return first is not None and day >= first


def load_goal_book(db: Session) -> GoalBook:
    return GoalBook(db.scalars(select(UserGoal)).all())


def effective_goals(db: Session, day: dt.date | None = None) -> dict[str, dict | None]:
    """Goals in force today (or on `day`) for every person. None for someone
    who has not set goals yet: nothing is judged or scored for them until they do."""
    day = day or config.today()
    book = load_goal_book(db)
    users = db.execute(select(User.id, User.name).order_by(User.name)).all()
    return {
        u.name: goal_dict(book.at(u.id, day)) if book.latest(u.id) is not None else None
        for u in users
    }
