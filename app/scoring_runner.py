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


def _last_row_dates(db: Session) -> dict[int, dt.date]:
    """The date of each person's newest row."""
    return dict(
        db.execute(
            select(DailyActivity.user_id, func.max(DailyActivity.date)).group_by(DailyActivity.user_id)
        ).all()
    )


def _is_final(last_row: dict[int, dt.date], user_id: int, day: dt.date, today: dt.date) -> bool:
    """A person's day is final once they have a row dated after it: each sync
    re-sends yesterday in full, so a later row means the day will not change. A
    day nobody synced after is treated as final (missing = zero) once
    READY_TIMEOUT_DAYS have passed, so one silent phone cannot hold everyone up."""
    if day >= today:
        return False
    if today >= day + dt.timedelta(days=scoring.READY_TIMEOUT_DAYS):
        return True
    last = last_row.get(user_id)
    return last is not None and last > day


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


def _pay_days(db: Session, users, book: GoalBook, days, dates, is_final=None) -> None:
    """Write each day's own ledger rows (calorie tier, workout day), stamped with
    that day. Days that already have a row are left alone, so this is safe to call
    for a week that was partly paid as its days settled. `is_final(user_id, day)`,
    when given, skips days that are not final yet."""
    for d in dates:
        period = f"day-{d.isoformat()}"
        for u in users:
            if not book.judged(u.id, d):
                continue
            if is_final is not None and not is_final(u.id, d):
                continue
            minutes, calories = days.get(u.id, {}).get(d, (0, 0))
            cal_pts, workout_pts = scoring.score_day(minutes, calories, book.at(u.id, d))
            add_earn(db, u.id, period, "calorie_daily", cal_pts)
            add_earn(db, u.id, period, "workout_day", workout_pts)


def _marker(period: str, user_id: int) -> str:
    """Key of a person's own "scored" marker for a week or month. It freezes that
    person's points the same way the group's marker freezes the group's."""
    return f"{period}:u{user_id}"


def _player_week(book: GoalBook, user, days, monday: dt.date) -> scoring.PlayerWeek:
    return scoring.score_player_week(
        {d: v for d, v in days.get(user.id, {}).items() if book.judged(user.id, d)},
        monday,
        lambda d, uid=user.id: book.at(uid, d),
    )


def _score_player_week(db: Session, user, book: GoalBook, days, monday: dt.date) -> None:
    """One person's own points for a finished week, paid as soon as THEIR data is
    final: any days not paid yet, a top-up for anything that rose after its day was
    paid (a late sync, labelled "Late Update"), and the personal weekly bonuses.
    The group's part (streak, multiplier, team bonus) comes later in _close_week."""
    period = f"wk-{monday.isoformat()}"
    w = _player_week(book, user, days, monday)
    _pay_days(db, [user], book, days, scoring.week_days(monday))
    paid = {
        r.category: int(r.pts)
        for r in db.execute(
            select(LedgerEntry.category, func.sum(LedgerEntry.points).label("pts"))
            .where(
                LedgerEntry.type == "earn",
                LedgerEntry.user_id == user.id,
                LedgerEntry.period.in_([f"day-{d.isoformat()}" for d in scoring.week_days(monday)]),
            )
            .group_by(LedgerEntry.category)
        )
    }
    for category in ("calorie_daily", "calorie_weekly", "workout_day", "workout_goal"):
        owed = getattr(w, category) - paid.get(category, 0)
        if category == "calorie_weekly":
            note = f"{w.calorie_days_hit} of 7 Days"
        elif category in ("calorie_daily", "workout_day"):
            note = "Late Update"  # only ever a top-up; the days have their own rows
        else:
            note = None
        add_earn(db, user.id, period, category, owed, note=note)
    db.add(ScoringPeriod(period=_marker(period, user.id), kind="week_user"))


def _close_week(db: Session, players, book: GoalBook, days, monday: dt.date, prev_streak: int) -> int:
    """The group's part of a week, once every player's own points are in: did the
    group succeed, the streak, the multiplier on what each person earned this
    week, and the team bonus. Returns the new streak."""
    period = f"wk-{monday.isoformat()}"
    weeks = {u.id: _player_week(book, u, days, monday) for u in players}
    full = [u for u in players if book.first_day(u.id) <= monday]
    pair_ok = bool(full) and all(weeks[u.id].ok for u in full)
    streak = prev_streak + 1 if pair_ok else 0
    mult = scoring.multiplier_for(streak)

    # The multiplier applies to what was actually paid for the week (its days plus
    # the weekly bonuses), so it always matches the ledger.
    periods = [f"day-{d.isoformat()}" for d in scoring.week_days(monday)] + [period]
    base = {
        r.user_id: int(r.pts)
        for r in db.execute(
            select(LedgerEntry.user_id, func.sum(LedgerEntry.points).label("pts"))
            .where(
                LedgerEntry.type == "earn",
                LedgerEntry.period.in_(periods),
                LedgerEntry.category.in_(
                    ("calorie_daily", "calorie_weekly", "workout_day", "workout_goal")
                ),
            )
            .group_by(LedgerEntry.user_id)
        )
    }
    weeks_word = "Week" if streak == 1 else "Weeks"
    for u in players:
        paid = base.get(u.id, 0)
        add_earn(
            db, u.id, period, "multiplier", scoring.scaled(paid, mult) - paid,
            note=f"x{mult} ({streak} {weeks_word} in a Row)",
        )

    if pair_ok:
        bonus = scoring.scaled(scoring.TEAM_BONUS, mult)  # every member gets the full bonus
        for u in players:
            add_earn(db, u.id, period, "team_bonus", bonus)

    db.add(
        ScoringPeriod(
            period=period, kind="week", pair_ok=pair_ok, streak=streak, multiplier=str(mult)
        )
    )
    return streak


def _pay_settled_days(
    db: Session, users, book: GoalBook, first_monday: dt.date, today: dt.date,
    last_row: dict[int, dt.date], marked: set[str],
) -> None:
    """Pay the plain daily points (calorie tier, workout day) for each person's
    days that are final but whose week has not been scored for them yet. Each day
    is paid once; the week-close step tops up anything that rose afterwards. A
    person whose week is already scored is frozen: nothing more is paid for it."""
    def payable(uid: int, d: dt.date) -> bool:
        week = f"wk-{(d - dt.timedelta(days=d.weekday())).isoformat()}"
        return _marker(week, uid) not in marked and _is_final(last_row, uid, d, today)

    last_day = today - dt.timedelta(days=1)
    if last_day < first_monday:
        return
    days = _load_days(db, first_monday, last_day)
    n = (last_day - first_monday).days + 1
    _pay_days(
        db, users, book, days,
        [first_monday + dt.timedelta(days=i) for i in range(n)],
        is_final=payable,
    )


def _score_player_month(db: Session, user, book: GoalBook, days, first: dt.date, last: dt.date, month: str) -> None:
    n = (last - first).days + 1
    # each day is judged against the calorie goal in force that day;
    # days before their goals started do not count
    hit = sum(
        1
        for day, (_, cal) in days.get(user.id, {}).items()
        if book.judged(user.id, day) and cal >= book.at(user.id, day).min_calories
    )
    add_earn(
        db, user.id, month, "calorie_monthly", scoring.monthly_points(hit, n),
        note=f"{hit} of {n} Days ({round(hit * 100 / n)}%)",
    )
    db.add(ScoringPeriod(period=_marker(month, user.id), kind="month_user"))


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
    marked = set(done)  # periods and per-person markers already scored
    scored: list[str] = []
    last_row = _last_row_dates(db)

    # Weeks run Monday to Sunday; the first one is the first full week on or
    # after the scoring start. Replayed in order so streaks carry correctly.
    # Each person's own points are paid as soon as THEIR data is final; the group
    # part (streak, multiplier, team bonus) waits until every player's are in, and
    # later weeks' group parts wait behind it because they continue its streak.
    monday = start + dt.timedelta(days=(7 - start.weekday()) % 7)
    streak = 0
    blocked_at = None  # the first week whose group part is still waiting
    while monday + dt.timedelta(days=6) < today:
        period = f"wk-{monday.isoformat()}"
        if period in done:
            streak = done[period].streak or 0
        else:
            sunday = monday + dt.timedelta(days=6)
            playing = [u for u in users if book.first_day(u.id) <= sunday]
            days = _load_days(db, monday, sunday)
            for u in playing:
                marker = _marker(period, u.id)
                if marker not in marked and _is_final(last_row, u.id, sunday, today):
                    _score_player_week(db, u, book, days, monday)
                    marked.add(marker)
            if blocked_at is None:
                if all(_marker(period, u.id) in marked for u in playing):
                    streak = _close_week(db, playing, book, days, monday, streak)
                    scored.append(period)
                else:
                    blocked_at = monday
        monday += dt.timedelta(days=7)

    # Days of weeks that are not scored yet are paid daily as they become final.
    _pay_settled_days(db, users, book, blocked_at or monday, today, last_row, marked)

    # Calendar months that start on/after the scoring start and have finished.
    # The monthly bonus is personal, so each person gets it once their own data is
    # final; the month is marked scored when everyone has been paid.
    y, m = start.year, start.month
    if start.day != 1:
        y, m = y + (m == 12), (m % 12) + 1
    while True:
        month = f"{y}-{m:02d}"
        first, last = scoring.month_bounds(month)
        if last >= today:
            break
        if month not in done:
            playing = [u for u in users if book.first_day(u.id) <= last]
            days = None
            for u in playing:
                marker = _marker(month, u.id)
                if marker not in marked and _is_final(last_row, u.id, last, today):
                    if days is None:
                        days = _load_days(db, first, last)
                    _score_player_month(db, u, book, days, first, last, month)
                    marked.add(marker)
            if all(_marker(month, u.id) in marked for u in playing):
                db.add(ScoringPeriod(period=month, kind="month"))
                scored.append(month)
        y, m = y + (m == 12), (m % 12) + 1

    db.commit()
    return scored
