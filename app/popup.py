"""The points popup: what landed for you since you last closed it.

"New" is tracked per person with three pointers on the users table:
- seen_ledger_id: the newest ledger row shown (ledger writes are serialized by the
  ledger lock, so ids are committed in order and "id > seen" is exact),
- seen_my_week: the newest week of your own that was scored and shown,
- seen_team_week: the newest team week result shown. A missed team week pays
  nothing, so this is the only way to know its result is new.

The popup only opens when you have new points. A team result with no points (a
missed week) waits and rides along in your next popup. Everything shown here is
about you; teammates' points and redemptions never open it.

build() returns the popup ready to draw (title, total, highlight boxes, rows) plus
`ack`, the pointers to send back when it is closed.
"""
import datetime as dt
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import scoring
from app.goals import load_goal_book
from app.ledger import current_balance
from app.models import LedgerEntry, ScoringPeriod, User
from app.scoring_runner import _load_days, _player_week

TIER_PCT = {pts: pct for pct, pts in scoring.CALORIE_TIERS}  # 1 -> 100, 2 -> 150, 3 -> 200
LEVEL_UPS = {n: m for n, m in scoring.MULTIPLIERS}  # streak -> the multiplier it unlocks
MAX_MULT = scoring.MULTIPLIERS[0][1]
MAX_MONTHLY = scoring.MONTHLY_TIERS[0][1]
WEEK_RE = re.compile(r"^wk-\d{4}-\d{2}-\d{2}$")


def _monday(period: str) -> dt.date:
    return dt.date.fromisoformat(period[3:13])


def _day(d: dt.date) -> str:
    return f"{d:%a}, {d:%b} {d.day}"  # Wed, Nov 11


def _range(dates: list[dt.date]) -> str:
    a, b = min(dates), max(dates)
    return f"{a:%b} {a.day}" if a == b else f"{a:%b} {a.day} – {b:%b} {b.day}"


def _month_name(period: str) -> str:
    return dt.date(int(period[:4]), int(period[5:7]), 1).strftime("%B")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _mult(m) -> str:
    return str(m)


def _streak_detail(streak: int, mult: str) -> str:
    if mult == "1.0":
        return f"{streak}-week streak"
    return f"{streak}-week streak · x{mult}" + (" (max)" if mult == _mult(MAX_MULT) else "")


def _next_hint(streak: int) -> str | None:
    ahead = sorted((n, m) for n, m in scoring.MULTIPLIERS if n > streak)
    if not ahead:
        return None
    n, m = ahead[0]
    k = n - streak
    return f"Win next week to unlock x{m}" if k == 1 else f"{k} more weeks for x{m}"


def _box(tone: str, title: str, detail: str | None = None, hint: str | None = None) -> dict:
    return {"tone": tone, "title": title, "detail": detail, "hint": hint}


def _short_reasons(pw: scoring.PlayerWeek, target: int) -> str:
    parts = []
    if pw.workout_days < target:
        parts.append(f"{pw.workout_days} of {target} workouts")
    if pw.calorie_days_hit < scoring.SUCCESS_MIN_CALORIE_DAYS:
        parts.append(f"{_plural(pw.calorie_days_hit, 'calorie day')} (needed {scoring.SUCCESS_MIN_CALORIE_DAYS})")
    return ", ".join(parts)


def build(db: Session, user: User) -> dict:
    seen_id = user.seen_ledger_id or 0
    rows = db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.type == "earn", LedgerEntry.user_id == user.id, LedgerEntry.id > seen_id)
        .order_by(LedgerEntry.id)
    ).all()
    if not rows:
        return {"popup": None}

    book = load_goal_book(db)
    first_day = book.first_day(user.id)
    first_points = not db.scalar(
        select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.type == "earn", LedgerEntry.user_id == user.id, LedgerEntry.id <= seen_id
        )
    )

    # Your own weeks that were scored since you last looked.
    suffix = f":u{user.id}"
    my_weeks = sorted(
        p.split(":")[0]
        for p in db.scalars(select(ScoringPeriod.period).where(ScoringPeriod.kind == "week_user"))
        if p.endswith(suffix) and p.split(":")[0] > (user.seen_my_week or "")
    )
    # Team week results since you last looked, only for weeks you were playing in.
    all_team = {
        p.period: p
        for p in db.scalars(select(ScoringPeriod).where(ScoringPeriod.kind == "week"))
    }
    team = [
        all_team[p] for p in sorted(all_team)
        if p > (user.seen_team_week or "")
        and first_day is not None and first_day <= _monday(p) + dt.timedelta(days=6)
    ]
    team_periods = [t.period for t in team]

    def player_week(period: str):
        monday = _monday(period)
        days = _load_days(db, monday, monday + dt.timedelta(days=6))
        pw = _player_week(book, user, days, monday)
        target = book.at(user.id, monday + dt.timedelta(days=6)).weekly_workouts
        grace = first_day is not None and first_day > monday
        return pw, target, grace

    def prev_streak(period: str) -> tuple[int, str]:
        prev = all_team.get(f"wk-{(_monday(period) - dt.timedelta(days=7)).isoformat()}")
        return (prev.streak or 0, prev.multiplier or "1.0") if prev else (0, "1.0")

    # ---- sort the new rows into groups ----
    day_cal, day_work, late, cal_wk, goal, mult, bonus, monthly, other = ([] for _ in range(9))
    for r in rows:
        p = r.period or ""
        if p.startswith("day-"):
            (day_cal if r.category == "calorie_daily" else day_work if r.category == "workout_day" else other).append(r)
        elif p.startswith("wk-"):
            {
                "calorie_daily": late, "workout_day": late, "calorie_weekly": cal_wk,
                "workout_goal": goal, "multiplier": mult, "team_bonus": bonus,
            }.get(r.category, other).append(r)
        elif r.category == "calorie_monthly":
            monthly.append(r)
        else:
            other.append(r)

    day_dates = sorted({dt.date.fromisoformat(r.period[4:]) for r in day_cal + day_work})
    weeks_new = sorted(set(my_weeks) | set(team_periods))
    long = len(weeks_new) >= 2 or len(monthly) >= 2
    one_week = weeks_new[0] if len(weeks_new) == 1 else None
    pws = {w: player_week(w) for w in my_weeks}
    for t in team_periods:
        pws.setdefault(t, player_week(t))
    current_streak = all_team[max(all_team)].streak or 0 if all_team else 0

    # ---- title ----
    def away() -> str:
        return f"While you were away ({_range(day_dates)})" if day_dates else "While you were away"

    team_won_only = len(team) == 1 and team[0].pair_ok and team[0].period not in my_weeks
    if first_points:
        title = "Your first points are in!"
    elif long:
        title = away()
    elif monthly:
        title = f"{_month_name(monthly[-1].period)} is closed"
    elif one_week and one_week in my_weeks and one_week in team_periods:
        title = "Last week is in"
    elif one_week and one_week in my_weeks:
        title = "Your week is in"
    elif team_won_only:
        title = "Team result is in"
    elif len(day_dates) == 1:
        title = f"Your points for {_day(day_dates[0])}"
    else:
        title = away()

    # ---- highlight boxes (up to two) ----
    boxes = []
    if first_points:
        boxes.append(_box("gold", "Welcome to the team.", "Your points go into the shared pot for rewards."))

    if long and team:
        won = sum(1 for t in team if t.pair_ok)
        last = team[-1]
        boxes.append(_box(
            "gold" if won else "plain",
            f"The team qualified {won} of {len(team)} weeks",
            _streak_detail(last.streak, last.multiplier) if last.streak else "The streak reset",
            _next_hint(last.streak) if last.streak else None,
        ))
    elif len(team) == 1:
        t = team[0]
        pw, target, grace = pws[t.period]
        mine_new = t.period in my_weeks
        short = not grace and not pw.ok
        if t.pair_ok:
            unlocked = LEVEL_UPS.get(t.streak)
            if unlocked is not None:
                pct = round((unlocked - 1) * 100)
                boxes.append(_box(
                    "perfect" if unlocked == MAX_MULT else "gold",
                    f"Top multiplier: x{unlocked}!" if unlocked == MAX_MULT else f"Streak unlocked: x{unlocked}!",
                    f"{t.streak}-week streak · last week’s points +{pct}%",
                    _next_hint(t.streak) or "The highest level. Keep it going.",
                ))
            else:
                boxes.append(_box("gold", "Team week succeeded!", _streak_detail(t.streak, t.multiplier), _next_hint(t.streak)))
        else:
            was, was_mult = prev_streak(t.period)
            if short:
                if mine_new:  # told once; a later team result does not repeat it
                    boxes.append(_box(
                        "plain", "You didn’t qualify this week", _short_reasons(pw, target),
                        "The streak resets." if was else None,
                    ))
            else:
                if was:
                    m = "" if was_mult == "1.0" else f", x{was_mult}"
                    detail = f"The streak resets (was {_plural(was, 'week')}{m}). Your own points still counted."
                else:
                    detail = "Your own points still counted."
                boxes.append(_box(
                    "plain",
                    "The team week didn’t qualify" if mine_new else "Last week’s team result: didn’t qualify",
                    detail,
                ))
    elif one_week and one_week in my_weeks:
        pw, target, grace = pws[one_week]
        if grace:
            boxes.append(_box("plain", "Your first week is done.", "You join the team check next Monday."))
        elif not pw.ok:
            boxes.append(_box(
                "plain", "You didn’t qualify this week", _short_reasons(pw, target),
                "No team bonus, and the streak resets." if current_streak else "No team bonus this week.",
            ))

    if monthly:
        m = monthly[-1]
        pct_m = re.search(r"(\d+)%", m.note or "")
        pct = pct_m.group(1) + "%" if pct_m else ""
        if long:
            boxes.append(_box("gold", f"{_month_name(m.period)} bonus: +{m.points}", f"{pct} of days"))
        elif m.points >= MAX_MONTHLY:
            boxes.append(_box("perfect", f"Perfect month! +{m.points}", "You hit your calorie goal every single day."))
        else:
            boxes.append(_box("gold", f"Monthly bonus: +{m.points}", f"You hit your calorie goal on {pct} of days."))

    if not long and one_week and one_week in my_weeks:
        pw, _, _ = pws[one_week]
        if pw.calorie_weekly and pw.workout_goal:
            perfect = pw.calorie_days_hit == 7
            boxes.append(_box(
                "perfect" if perfect else "gold", "You hit both weekly goals!",
                "Perfect calorie week." if perfect else None,
            ))
    boxes = boxes[:2]

    # ---- rows, grouped by type ----
    out = []

    def add(name, group, sub_one, sub_many):
        if group:
            out.append({
                "name": name,
                "points": sum(r.points for r in group),
                "sub": sub_one(group[0]) if len(group) == 1 else sub_many(group),
            })

    add("Daily calorie goals", day_cal,
        lambda r: f"{_day(dt.date.fromisoformat(r.period[4:]))}, {TIER_PCT.get(r.points, 100)}% of goal",
        lambda g: f"{len(g)} days, best {max(TIER_PCT.get(r.points, 100) for r in g)}%")
    add("Complete workouts", day_work,
        lambda r: _day(dt.date.fromisoformat(r.period[4:])),
        lambda g: f"{len(g)} days")
    add("Late update", late,
        lambda r: "a day went up after it was paid",
        lambda g: "days that went up after they were paid")
    add("Weekly calorie bonus", cal_wk,
        lambda r: (r.note or "").lower(),
        lambda g: _plural(len(g), "week"))

    def goal_sub(r):
        pw, target, _ = pws.get(r.period) or player_week(r.period)
        return f"{pw.workout_days} of {target} workouts"
    add("Workout goal", goal, goal_sub, lambda g: _plural(len(g), "week"))
    add("Streak multiplier", mult,
        lambda r: f"{(r.note or '').split(' ')[0]} on that week’s points",
        lambda g: _plural(len(g), "week"))
    add("Team bonus", bonus, lambda r: "everyone qualified", lambda g: _plural(len(g), "week"))
    if not bonus and not long and one_week and one_week in my_weeks and one_week not in all_team:
        pw, _, grace = pws[one_week]
        if grace:
            out.append({"name": "Team bonus", "points": None, "sub": "you get it if the team succeeds"})
        elif pw.ok:
            out.append({"name": "Team bonus", "points": None, "sub": "you qualified; waiting on the team’s week"})
    add("Monthly calorie bonus", monthly,
        lambda r: (r.note or "").lower(),
        lambda g: _plural(len(g), "month"))
    add("Other points", other, lambda r: "", lambda g: "")

    return {
        "popup": {
            "title": title,
            "total": sum(r.points for r in rows),
            "highlights": boxes,
            "rows": out,
            "balance": current_balance(db),
        },
        "ack": {
            "ledger_id": rows[-1].id,
            "my_week": my_weeks[-1] if my_weeks else None,
            "team_week": team_periods[-1] if team_periods else None,
        },
    }


def mark_seen(db: Session, user: User, ledger_id: int | None, my_week: str | None, team_week: str | None) -> None:
    """Move the pointers forward (never back), to what the closed popup showed."""
    if ledger_id is not None and ledger_id > (user.seen_ledger_id or 0):
        user.seen_ledger_id = ledger_id
    if my_week and WEEK_RE.match(my_week) and my_week > (user.seen_my_week or ""):
        user.seen_my_week = my_week
    if team_week and WEEK_RE.match(team_week) and team_week > (user.seen_team_week or ""):
        user.seen_team_week = team_week
    db.commit()
