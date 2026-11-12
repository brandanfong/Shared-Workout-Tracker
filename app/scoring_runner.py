"""Scoring runner: turns closed weeks/months into ledger entries.

The rules themselves (points per tier, multipliers, ...) live in scoring.py;
this module reads the database, applies them, and writes the ledger.
"""
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config, scoring
from app.goals import GoalBook, load_goal_book
from app.ledger import add_earn, lock_ledger
from app.models import DailyActivity, LedgerEntry, ScoringPeriod, User


def _day_synced(db: Session, users, day: dt.date, today: dt.date) -> bool:
    """A period is ready once every player has a row for its last day (so the
    Monday sync had a chance to land), or after READY_TIMEOUT_DAYS regardless."""
    if today >= day + dt.timedelta(days=scoring.READY_TIMEOUT_DAYS):
        return True
    have = set(
        db.scalars(select(DailyActivity.user_id).where(DailyActivity.date == day))
    )
    return all(u.id in have for u in users)


def _load_days(db: Session, start: dt.date, end: dt.date) -> dict[int, dict[dt.date, tuple[int, int]]]:
    out: dict[int, dict[dt.date, tuple[int, int]]] = {}
    rows = db.execute(
        select(
            DailyActivity.user_id,
            DailyActivity.date,
            DailyActivity.exercise_minutes,
            DailyActivity.active_calories,
        ).where(DailyActivity.date >= start, DailyActivity.date <= end)
    ).all()
    for r in rows:
        out.setdefault(r.user_id, {})[r.date] = (r.exercise_minutes, r.active_calories)
    return out


def _pay_days(db: Session, users, book: GoalBook, days, dates) -> None:
    """Write each day's own ledger rows (calorie tier, workout day), stamped with
    that day. Days that already have a row are left alone, so this is safe to call
    for a week that was partly paid as its days settled."""
    for d in dates:
        period = f"day-{d.isoformat()}"
        for u in users:
            if not book.judged(u.id, d):
                continue
            minutes, calories = days.get(u.id, {}).get(d, (0, 0))
            cal_pts, workout_pts = scoring.score_day(minutes, calories, book.at(u.id, d))
            add_earn(db, u.id, period, "calorie_daily", cal_pts)
            add_earn(db, u.id, period, "workout_day", workout_pts)


def _score_week(db: Session, users, book: GoalBook, monday: dt.date, prev_streak: int) -> int:
    """Write one week's ledger rows and its scoring_periods marker. Returns the
    pair's new streak."""
    period = f"wk-{monday.isoformat()}"
    sunday = monday + dt.timedelta(days=6)
    # Someone whose goals start after this week is not part of it at all. Someone
    # who started mid-week is in their grace week: they earn their own points from
    # their first day (and get the team bonus if the group succeeds) but are left
    # out of the group check and the streak until the next Monday.
    users = [u for u in users if book.first_day(u.id) <= sunday]
    days = _load_days(db, monday, sunday)
    weeks = {
        u.id: scoring.score_player_week(
            {d: v for d, v in days.get(u.id, {}).items() if book.judged(u.id, d)},
            monday,
            lambda d, uid=u.id: book.at(uid, d),
        )
        for u in users
    }
    full = [u for u in users if book.first_day(u.id) <= monday]
    pair_ok = bool(full) and all(weeks[u.id].ok for u in full)
    streak = prev_streak + 1 if pair_ok else 0
    mult = scoring.multiplier_for(streak)

    # Any day of this week not yet paid gets its own row now (a bulk catch-up reads
    # the same as live days). Then anything that ROSE after its day was paid, such
    # as a late sync, is added once as a "Late Update" under the week.
    _pay_days(db, users, book, days, scoring.week_days(monday))
    paid = {
        (r.user_id, r.category): int(r.pts)
        for r in db.execute(
            select(
                LedgerEntry.user_id,
                LedgerEntry.category,
                func.sum(LedgerEntry.points).label("pts"),
            )
            .where(
                LedgerEntry.type == "earn",
                LedgerEntry.period.in_([f"day-{d.isoformat()}" for d in scoring.week_days(monday)]),
            )
            .group_by(LedgerEntry.user_id, LedgerEntry.category)
        )
    }

    for u in users:
        w = weeks[u.id]
        for category in ("calorie_daily", "calorie_weekly", "workout_day", "workout_goal"):
            owed = getattr(w, category) - paid.get((u.id, category), 0)
            if category == "calorie_weekly":
                note = f"{w.calorie_days_hit} of 7 Days"
            elif category in ("calorie_daily", "workout_day"):
                note = "Late Update"  # only ever a top-up; the days have their own rows
            else:
                note = None
            add_earn(db, u.id, period, category, owed, note=note)
        extra = scoring.scaled(w.subtotal, mult) - w.subtotal
        weeks_word = "Week" if streak == 1 else "Weeks"
        add_earn(
            db, u.id, period, "multiplier", extra,
            note=f"x{mult} ({streak} {weeks_word} in a Row)",
        )

    if pair_ok:
        bonus = scoring.scaled(scoring.TEAM_BONUS, mult)  # every member gets the full bonus
        for u in users:
            add_earn(db, u.id, period, "team_bonus", bonus)

    db.add(
        ScoringPeriod(
            period=period, kind="week", pair_ok=pair_ok, streak=streak, multiplier=str(mult)
        )
    )
    return streak


def _pay_settled_days(db: Session, users, book: GoalBook, first_monday: dt.date, today: dt.date) -> None:
    """Pay the plain daily points (calorie tier, workout day) for days that have
    settled but whose week has not been scored yet. A day settles two days after
    it ends. Each day is paid once; the week-close step tops up anything that
    rose afterwards."""
    last_day = today - dt.timedelta(days=scoring.GRACE_DAYS + 1)
    if last_day < first_monday:
        return
    days = _load_days(db, first_monday, last_day)
    n = (last_day - first_monday).days + 1
    _pay_days(db, users, book, days, [first_monday + dt.timedelta(days=i) for i in range(n)])


def _score_month(db: Session, users, book: GoalBook, month: str) -> None:
    first, last = scoring.month_bounds(month)
    days = _load_days(db, first, last)
    n = (last - first).days + 1
    for u in users:
        if book.first_day(u.id) > last:
            continue  # not playing yet that month
        # each day is judged against the calorie goal in force that day;
        # days before their goals started do not count
        hit = sum(
            1
            for day, (_, cal) in days.get(u.id, {}).items()
            if book.judged(u.id, day) and cal >= book.at(u.id, day).min_calories
        )
        add_earn(
            db, u.id, month, "calorie_monthly", scoring.monthly_points(hit, n),
            note=f"{hit} of {n} Days ({round(hit * 100 / n)}%)",
        )
    db.add(ScoringPeriod(period=month, kind="month"))


def run_scoring(db: Session, today: dt.date | None = None) -> list[str]:
    """Score every closed week/month that has not been scored yet, in order.
    Safe to call any time and as often as you like: scored periods are skipped,
    so it is idempotent. Returns the periods scored by this call."""
    if not config.SCORING_ENABLED:
        return []
    today = today or config.today()
    start = config.SCORING_START
    lock_ledger(db)
    book = load_goal_book(db)
    # Nobody earns points (or holds up the team's week) until they have set goals.
    users = [
        u for u in db.execute(select(User).order_by(User.name)).scalars().all()
        if book.latest(u.id) is not None
    ]
    if not users:
        db.rollback()
        return []
    done = {
        p.period: p for p in db.scalars(select(ScoringPeriod))
    }
    scored: list[str] = []

    # Weeks run Monday to Sunday; the first one is the first full week on or
    # after the scoring start. Replayed in order so streaks carry correctly.
    monday = start + dt.timedelta(days=(7 - start.weekday()) % 7)
    streak = 0
    while scoring.week_ready_date(monday) <= today:
        period = f"wk-{monday.isoformat()}"
        if period in done:
            streak = done[period].streak or 0
        else:
            sunday = monday + dt.timedelta(days=6)
            playing = [u for u in users if book.first_day(u.id) <= sunday]
            if not _day_synced(db, playing, sunday, today):
                break  # later weeks depend on this one's streak, so wait
            streak = _score_week(db, users, book, monday, streak)
            scored.append(period)
        monday += dt.timedelta(days=7)

    # `monday` is now the first week that is not scored yet (still open, or
    # waiting on a sync). Its settled days are paid daily in the meantime.
    _pay_settled_days(db, users, book, monday, today)

    # Calendar months that start on/after the scoring start and have finished.
    y, m = start.year, start.month
    if start.day != 1:
        y, m = y + (m == 12), (m % 12) + 1
    while True:
        month = f"{y}-{m:02d}"
        _, last = scoring.month_bounds(month)
        if last + dt.timedelta(days=1 + scoring.GRACE_DAYS) > today:
            break
        playing = [u for u in users if book.first_day(u.id) <= last]
        if month not in done and _day_synced(db, playing, last, today):
            _score_month(db, users, book, month)
            scored.append(month)
        y, m = y + (m == 12), (m % 12) + 1

    db.commit()
    return scored
