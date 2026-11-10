import datetime as dt
import hashlib
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey,
                        Index, Integer, String, UniqueConstraint, case,
                        create_engine, func, inspect, or_, select, text)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import colors
import labels
import scoring

# Tolerate stray quotes/whitespace and a plain postgresql:// prefix
raw_url = os.environ["DATABASE_URL"].strip().strip("'\"")
if raw_url.startswith("postgresql://"):
    raw_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(raw_url, pool_pre_ping=True)

STATIC_DIR = Path(__file__).parent / "static"

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


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    # Chosen display color (#rrggbb). None means "automatic".
    color: Mapped[str | None] = mapped_column(String, nullable=True)


class ApiKey(Base):
    """One row per key. Only a hash is stored, never the key itself."""

    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String, unique=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


EPOCH = dt.date(1970, 1, 1)


class UserGoal(Base):
    """History of each person's goals. A person's goals on any day are the row
    with the latest effective_from that is on or before that day. Minutes and
    calories are read for the exact day being judged; the weekly workout target
    is read for the Sunday the week ended. People with no rows use the
    defaults above."""

    __tablename__ = "user_goals"
    __table_args__ = (UniqueConstraint("user_id", "effective_from"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    effective_from: Mapped[dt.date] = mapped_column(Date)
    min_minutes: Mapped[int] = mapped_column(Integer)
    min_calories: Mapped[int] = mapped_column(Integer)
    weekly_workouts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyActivity(Base):
    __tablename__ = "daily_activity"
    __table_args__ = (UniqueConstraint("user_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)  # the person's local calendar day
    exercise_minutes: Mapped[int] = mapped_column(Integer)
    active_calories: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


COST_MIN = 1
COST_MAX = 5000

STARTER_REWARDS = [
    # (name, cost, cooldown_days)
    ("Coffee or dessert date", 50, 0),
    ("Movie night, winner picks", 75, 0),
    ("Home-cooked dinner by the other", 100, 0),
    ("$25 gift", 200, 7),
    ("Nice dinner out", 325, 14),
    ("Spa day or day trip", 500, 30),
    ("Weekend getaway fund", 1000, 60),
]


class Reward(Base):
    """The prize menu. Either person can add, edit, or deactivate rewards."""

    __tablename__ = "rewards"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    cost: Mapped[int] = mapped_column(Integer)
    repeatable: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LedgerEntry(Base):
    """Append-only audit trail of every change to the shared balance.
    Rows are never updated or deleted; mistakes are fixed with an 'adjust' row.
    balance = SUM(points)."""

    __tablename__ = "ledger"
    __table_args__ = (
        CheckConstraint("type in ('earn', 'redeem', 'adjust')", name="ck_ledger_type"),
        CheckConstraint(
            "(type = 'earn' and points > 0) or (type = 'redeem' and points < 0) "
            "or (type = 'adjust' and points <> 0)",
            name="ck_ledger_sign",
        ),
        # A re-run of a week's scoring can't double-pay: one earn per
        # person + period + category.
        Index(
            "uq_ledger_earn",
            "user_id",
            "period",
            "category",
            unique=True,
            postgresql_where=text("type = 'earn'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    type: Mapped[str] = mapped_column(String)  # earn | redeem | adjust
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    period: Mapped[str | None] = mapped_column(String, nullable=True)  # wk-2026-11-02, 2026-11
    category: Mapped[str] = mapped_column(String)  # calorie_daily, workout_day, redeem, ...
    points: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reward_id: Mapped[int | None] = mapped_column(ForeignKey("rewards.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)


class ScoringPeriod(Base):
    """One row per week/month once it has been scored. A period is scored once
    and then frozen: data that arrives later never changes points already paid.
    Also carries the pair's streak so the next week can continue from it."""

    __tablename__ = "scoring_periods"
    period: Mapped[str] = mapped_column(String, primary_key=True)  # wk-2026-11-02 | 2026-11
    kind: Mapped[str] = mapped_column(String)  # week | month
    pair_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    streak: Mapped[int | None] = mapped_column(Integer, nullable=True)
    multiplier: Mapped[str | None] = mapped_column(String, nullable=True)
    scored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LogIn(BaseModel):
    # The user now comes from the API key. A "name" field sent by an older
    # Shortcut is simply ignored.
    date: dt.date
    minutes: int = Field(ge=0, le=1440)
    calories: int = Field(ge=0, le=20000)

    @field_validator("minutes", "calories", mode="before")
    @classmethod
    def round_to_int(cls, v):
        try:
            return round(float(v))
        except (TypeError, ValueError):
            raise ValueError("must be a number")


class SettingsIn(BaseModel):
    min_minutes: int | None = Field(default=None, ge=1, le=1440)
    min_calories: int | None = Field(default=None, ge=1, le=20000)
    weekly_workouts: int | None = Field(default=None, ge=1, le=7)


class RewardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cost: int = Field(ge=COST_MIN, le=COST_MAX)
    repeatable: bool = True
    cooldown_days: int = Field(default=0, ge=0, le=365)


class RewardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cost: int | None = Field(default=None, ge=COST_MIN, le=COST_MAX)
    repeatable: bool | None = None
    cooldown_days: int | None = Field(default=None, ge=0, le=365)
    active: bool | None = None


class AdjustIn(BaseModel):
    points: int = Field(ge=-100000, le=100000)
    note: str = Field(min_length=1, max_length=500)  # why, required for audit


app = FastAPI(title="Couples Workout Tracker")
Base.metadata.create_all(engine)  # swap for Alembic migrations later


def migrate_user_color() -> None:
    """create_all does not add columns to existing tables."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS color VARCHAR"))


migrate_user_color()


def migrate_user_settings() -> None:
    """One-time: copy the old single-row-per-person user_settings into the goal
    history as their starting version. Safe to run on every startup. The old
    table is left in place, unused."""
    insp = inspect(engine)
    if not insp.has_table("user_settings"):
        return
    cols = {c["name"] for c in insp.get_columns("user_settings")}
    weekly = "s.weekly_workouts" if "weekly_workouts" in cols else str(DEFAULT_WEEKLY_WORKOUTS)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_goals (user_id, effective_from, min_minutes, min_calories, weekly_workouts) "
                f"SELECT s.user_id, DATE '1970-01-01', s.min_minutes, s.min_calories, {weekly} "
                "FROM user_settings s "
                "WHERE NOT EXISTS (SELECT 1 FROM user_goals g WHERE g.user_id = s.user_id)"
            )
        )


migrate_user_settings()


def seed_rewards() -> None:
    """Fill the menu with the starter rewards, only if it has never had any."""
    with Session(engine) as db:
        if db.scalar(select(func.count()).select_from(Reward)):
            return
        db.add_all(
            Reward(name=n, cost=c, cooldown_days=cd, repeatable=True)
            for n, c, cd in STARTER_REWARDS
        )
        db.commit()


seed_rewards()


def get_session():
    with Session(engine) as session:
        yield session


def hash_key(key: str) -> str:
    # Keys are long and random, so a plain SHA-256 is enough (bcrypt/argon2
    # are for human-chosen passwords).
    return hashlib.sha256(key.encode()).hexdigest()


def issue_key(db: Session, name: str, label: str | None = None) -> str:
    """Create a new key for a person (creating the person if needed).
    Returns the raw key. It is shown once and cannot be recovered."""
    name = name.strip().lower()
    user = db.scalar(select(User).where(User.name == name))
    if user is None:
        user = User(name=name)
        db.add(user)
        db.flush()
    key = secrets.token_urlsafe(32)
    db.add(ApiKey(user_id=user.id, key_hash=hash_key(key), label=label))
    db.commit()
    return key


def current_user(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> User:
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="bad key")
    user = db.scalar(
        select(User)
        .join(ApiKey, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == hash_key(x_api_key), ApiKey.revoked_at.is_(None))
    )
    if user is None:
        raise HTTPException(status_code=401, detail="bad key")
    return user


DEFAULT_GOALS = scoring.Goals(DEFAULT_MIN_MINUTES, DEFAULT_MIN_CALORIES, DEFAULT_WEEKLY_WORKOUTS)


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


def load_goal_book(db: Session) -> GoalBook:
    return GoalBook(db.scalars(select(UserGoal)).all())


def effective_goals(db: Session, day: dt.date | None = None) -> dict[str, dict]:
    """Goals in force today (or on `day`) for every person."""
    day = day or _today()
    book = load_goal_book(db)
    users = db.execute(select(User.id, User.name).order_by(User.name)).all()
    return {u.name: goal_dict(book.at(u.id, day)) for u in users}


def next_monday(day: dt.date) -> dt.date:
    return day + dt.timedelta(days=7 - day.weekday())


@app.get("/")
def dashboard():
    # The page itself contains no secrets; it asks for your key and sends it
    # with each request to the /api endpoints.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/log")
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


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)):
    """Your goals as they stand today, and whether scoring is switched on."""
    return {
        "name": user.name,
        **goal_dict(load_goal_book(db).at(user.id, _today())),
        "scoring_enabled": SCORING_ENABLED,
        "color": effective_colors(db)[user.name],
    }


@app.patch("/api/me/settings")
def update_my_settings(
    body: SettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    """Saves goals. Minutes and calories apply from today; each day is judged
    by the goals in force that day. The weekly workout target counts for the
    whole week you save it in and locks when that week ends. Saving again the
    same day replaces that day's entry. Your very first save replaces the
    defaults for all time."""
    today = _today()
    book = load_goal_book(db)
    base = book.latest(user.id)
    first_save = base is None
    base = base or DEFAULT_GOALS
    effective_from = EPOCH if first_save else today

    merged = scoring.Goals(
        body.min_minutes if body.min_minutes is not None else base.min_minutes,
        body.min_calories if body.min_calories is not None else base.min_calories,
        body.weekly_workouts if body.weekly_workouts is not None else base.weekly_workouts,
    )
    row = db.scalar(
        select(UserGoal).where(
            UserGoal.user_id == user.id, UserGoal.effective_from == effective_from
        )
    )
    if row is None:
        row = UserGoal(user_id=user.id, effective_from=effective_from)
        db.add(row)
    row.min_minutes = merged.min_minutes
    row.min_calories = merged.min_calories
    row.weekly_workouts = merged.weekly_workouts
    db.commit()
    return {"name": user.name, **goal_dict(merged), "effective_from": effective_from.isoformat()}


def effective_colors(db: Session) -> dict[str, str]:
    """Everyone's display color: the one they picked, else an automatic one
    based on their position by name."""
    users = db.execute(select(User.name, User.color).order_by(User.name)).all()
    palette = colors.DEFAULT_PALETTE
    return {u.name: u.color or palette[i % len(palette)] for i, u in enumerate(users)}


class ColorIn(BaseModel):
    color: str | None = None  # null goes back to the automatic color


@app.get("/api/colors")
def color_options(user: User = Depends(current_user), db: Session = Depends(get_session)):
    """Preset swatches, which of them are still free, and your current color."""
    current = effective_colors(db)
    others = {n: c for n, c in current.items() if n != user.name}
    return {
        "mine": current[user.name],
        "custom": user.color is not None,
        "presets": [
            {"color": c, "available": colors.check(c, others) is None} for c in colors.PRESETS
        ],
    }


@app.patch("/api/me/color")
def set_my_color(
    body: ColorIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    """Pick your color (any #rrggbb that passes the checks) or send null to go
    back to the automatic one."""
    if body.color is None:
        user.color = None
    else:
        color = colors.normalize(body.color)
        if color is None:
            raise HTTPException(status_code=422, detail="Colors look like #3b82f6.")
        others = {n: c for n, c in effective_colors(db).items() if n != user.name}
        problem = colors.check(color, others)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        user.color = color
    db.commit()
    return {"color": effective_colors(db)[user.name], "custom": user.color is not None}


MAX_RANGE_DAYS = 730


@app.get("/api/activity", dependencies=[Depends(current_user)])
def activity(
    days: int = Query(30, ge=1, le=365),
    start: dt.date | None = None,
    end: dt.date | None = None,
    db: Session = Depends(get_session),
):
    """Daily activity for the charts. Either the last `days` days, or a custom
    range with `start` and/or `end` (both inclusive; `end` defaults to today and
    `start` to `days` before it)."""
    today = _today()
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


@app.get("/api/calendar", dependencies=[Depends(current_user)])
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
        g = book.at(r.uid, r.date)
        days.setdefault(r.date.isoformat(), {})[r.name] = {
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
            "workout": scoring.is_workout_day(r.exercise_minutes, r.active_calories, g),
            # The two halves of a workout day, so the calendar can show partial days.
            "calorie_hit": r.active_calories >= g.min_calories,
            "minutes_hit": r.exercise_minutes >= g.min_minutes,
        }

    return {
        "month": month,
        "users": users,
        "goals": goals,
        "colors": effective_colors(db),
        "everyone_color": colors.EVERYONE_COLOR,
        "days": days,
    }

# ---------------------------------------------------------------------------
# Points ledger and rewards store
# ---------------------------------------------------------------------------

LEDGER_LOCK_KEY = 7001  # serializes ledger writes so balance_after stays correct


def _lock_ledger(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LEDGER_LOCK_KEY})


def current_balance(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.points), 0))))


def _append(
    db: Session,
    *,
    type: str,
    user_id: int,
    category: str,
    points: int,
    period: str | None = None,
    reward_id: int | None = None,
    note: str | None = None,
) -> LedgerEntry:
    """Add one ledger row. Caller must hold the ledger lock and commit."""
    entry = LedgerEntry(
        type=type,
        user_id=user_id,
        period=period,
        category=category,
        points=points,
        balance_after=current_balance(db) + points,
        reward_id=reward_id,
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def _add_earn(
    db: Session,
    user_id: int,
    period: str,
    category: str,
    points: int,
    note: str | None = None,
) -> LedgerEntry | None:
    """Add an earn row unless it is empty or already there. Caller holds the
    ledger lock and commits."""
    if points <= 0:
        return None
    exists = db.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.type == "earn",
            LedgerEntry.user_id == user_id,
            LedgerEntry.period == period,
            LedgerEntry.category == category,
        )
    )
    if exists:
        return None
    return _append(
        db, type="earn", user_id=user_id, period=period,
        category=category, points=points, note=note,
    )


def record_earn(
    db: Session,
    user_id: int,
    period: str,
    category: str,
    points: int,
    note: str | None = None,
) -> LedgerEntry | None:
    """Credit points once. Idempotent: a second call for the same person +
    period + category is a no-op (returns None)."""
    _lock_ledger(db)
    entry = _add_earn(db, user_id, period, category, points, note)
    if entry is None:
        db.rollback()  # release the lock
        return None
    db.commit()
    return entry


def _entry_json(e: LedgerEntry, player: str, reward: str | None) -> dict:
    return {
        "id": e.id,
        "created_at": e.created_at.isoformat(),
        "type": e.type,
        "player": player,
        "period": e.period,
        "category": e.category,
        "points": e.points,
        "balance_after": e.balance_after,
        "reward_id": e.reward_id,
        "reward": reward,
        "note": e.note,
        "what": labels.entry_what(e.type, e.category, e.period, e.note, reward),
    }


def _reward_json(r: Reward, adder: str | None, last: dt.datetime | None = None) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cost": r.cost,
        "repeatable": r.repeatable,
        "cooldown_days": r.cooldown_days,
        "active": r.active,
        "added_by": adder,
        "last_redeemed_at": last.isoformat() if last else None,
    }


@app.get("/api/balance", dependencies=[Depends(current_user)])
def balance(db: Session = Depends(get_session)):
    per_person = db.execute(
        select(User.name, func.coalesce(func.sum(LedgerEntry.points), 0).label("pts"))
        .join(LedgerEntry, LedgerEntry.user_id == User.id)
        .where(LedgerEntry.type == "earn")
        .group_by(User.name)
        .order_by(User.name)
    ).all()
    return {
        "balance": current_balance(db),
        "earned_by": {r.name: int(r.pts) for r in per_person},
    }


@app.get("/api/rewards", dependencies=[Depends(current_user)])
def list_rewards(include_inactive: bool = False, db: Session = Depends(get_session)):
    last = (
        select(LedgerEntry.reward_id, func.max(LedgerEntry.created_at).label("at"))
        .where(LedgerEntry.type == "redeem")
        .group_by(LedgerEntry.reward_id)
        .subquery()
    )
    q = (
        select(Reward, User.name, last.c.at)
        .outerjoin(User, User.id == Reward.added_by)
        .outerjoin(last, last.c.reward_id == Reward.id)
        .order_by(Reward.cost, Reward.id)
    )
    if not include_inactive:
        q = q.where(Reward.active.is_(True))
    return [_reward_json(r, adder, at) for r, adder, at in db.execute(q).all()]


@app.post("/api/rewards", status_code=201)
def add_reward(
    body: RewardIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    r = Reward(
        name=body.name.strip(),
        cost=body.cost,
        repeatable=body.repeatable,
        cooldown_days=body.cooldown_days,
        added_by=user.id,
    )
    db.add(r)
    db.commit()
    return _reward_json(r, user.name)


@app.patch("/api/rewards/{reward_id}")
def update_reward(
    reward_id: int,
    body: RewardUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    r = db.get(Reward, reward_id)
    if r is None:
        raise HTTPException(status_code=404, detail="reward not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(r, field, value.strip() if field == "name" else value)
    db.commit()
    adder = db.scalar(select(User.name).where(User.id == r.added_by))
    return _reward_json(r, adder)


@app.post("/api/rewards/{reward_id}/redeem")
def redeem_reward(
    reward_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    _lock_ledger(db)  # serialize so two redeems cannot overspend the balance
    reward = db.get(Reward, reward_id)
    if reward is None or not reward.active:
        raise HTTPException(status_code=404, detail="reward not available")

    last = db.scalar(
        select(func.max(LedgerEntry.created_at)).where(
            LedgerEntry.type == "redeem", LedgerEntry.reward_id == reward.id
        )
    )
    if last is not None:
        if not reward.repeatable:
            raise HTTPException(status_code=409, detail="already redeemed (one-time reward)")
        ready = last + dt.timedelta(days=reward.cooldown_days)
        if reward.cooldown_days and dt.datetime.now(dt.timezone.utc) < ready:
            raise HTTPException(
                status_code=409,
                detail=f"on cooldown until {ready.isoformat()}",
            )

    bal = current_balance(db)
    if bal < reward.cost:
        raise HTTPException(
            status_code=409,
            detail=f"not enough points: balance {bal}, cost {reward.cost}",
        )

    entry = _append(
        db,
        type="redeem",
        user_id=user.id,
        category="redeem",
        points=-reward.cost,
        reward_id=reward.id,
        note=reward.name,
    )
    db.commit()
    return _entry_json(entry, user.name, reward.name)


@app.post("/api/adjust", status_code=201)
def adjust(
    body: AdjustIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    """Fix a mistake with a new row (never by editing history)."""
    _lock_ledger(db)
    if body.points == 0 or current_balance(db) + body.points < 0:
        raise HTTPException(status_code=409, detail="balance cannot go below zero")
    entry = _append(
        db, type="adjust", user_id=user.id, category="adjust",
        points=body.points, note=body.note,
    )
    db.commit()
    return _entry_json(entry, user.name, None)


def _earned_month():
    """The YYYY-MM an entry belongs to: the month it was earned FOR (read from its
    period, so a day paid two days late still counts in its own month), or for
    redemptions and adjustments the month they happened."""
    p = LedgerEntry.period
    return case(
        (or_(p.like("day-%"), p.like("wk-%")), func.substr(p, func.strpos(p, "-") + 1, 7)),
        (p.op("~")(r"^\d{4}-\d{2}$"), p),
        else_=func.to_char(func.timezone("UTC", LedgerEntry.created_at), "YYYY-MM"),
    )


def _like_pattern(text_: str) -> str:
    """Turn what someone typed into a safe 'contains' pattern."""
    escaped = text_.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _searchable_text():
    """One string per row holding what the history table shows (who, the sentence
    for what happened, the reward, the note), so searching finds what you can
    see. Dates also match as month names ("november") and short dates ("nov 4")."""
    p = LedgerEntry.period
    category_label = case(
        *[(LedgerEntry.category == k, v) for k, v in labels.CATEGORY_LABELS.items()],
        else_=func.replace(LedgerEntry.category, "_", " "),
    )
    type_label = case(
        (LedgerEntry.type == "earn", "earned"),
        (LedgerEntry.type == "redeem", "redeemed"),
        else_="adjustment",
    )
    period_label = case(
        (p.like("wk-%"), func.concat("week of ", func.substr(p, 4))),
        (p.like("day-%"), func.substr(p, 5)),
        else_=func.coalesce(p, ""),
    )
    day_words = case(
        (
            or_(p.like("day-%"), p.like("wk-%")),
            func.to_char(
                func.to_date(func.substr(p, func.strpos(p, "-") + 1, 10), "YYYY-MM-DD"),
                "FMMon FMDD FMMonth YYYY FMMon FMDD, YYYY",
            ),
        ),
        (
            p.op("~")(r"^\d{4}-\d{2}$"),
            func.to_char(func.to_date(func.concat(p, "-01"), "YYYY-MM-DD"), "FMMon FMMonth YYYY"),
        ),
        else_="",
    )
    return func.concat_ws(
        " ",
        User.name,
        type_label,
        category_label,
        period_label,
        day_words,
        func.coalesce(Reward.name, ""),
        func.coalesce(LedgerEntry.note, ""),
    )


@app.get("/api/ledger", dependencies=[Depends(current_user)])
def ledger(
    player: str | None = None,
    type: str | None = Query(None, pattern="^(earn|redeem|adjust)$"),
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    q: str | None = Query(None, max_length=100),
    before: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_session),
):
    """The points history, newest first. Filters combine. `before` is the id of
    the oldest entry you already have, to fetch the next page."""
    stmt = (
        select(LedgerEntry, User.name, Reward.name)
        .join(User, User.id == LedgerEntry.user_id)
        .outerjoin(Reward, Reward.id == LedgerEntry.reward_id)
        .order_by(LedgerEntry.id.desc())
        .limit(limit)
    )
    if player:
        stmt = stmt.where(User.name == player.strip().lower())
    if type:
        stmt = stmt.where(LedgerEntry.type == type)
    if month:
        stmt = stmt.where(_earned_month() == month)
    if q and q.strip():
        stmt = stmt.where(_searchable_text().ilike(_like_pattern(q), escape="\\"))
    if before:
        stmt = stmt.where(LedgerEntry.id < before)
    return [_entry_json(e, p, rw) for e, p, rw in db.execute(stmt).all()]


@app.get("/api/ledger/months", dependencies=[Depends(current_user)])
def ledger_months(db: Session = Depends(get_session)):
    """Months that have any history, newest first, for the month filter."""
    sub = select(_earned_month().label("m")).subquery()
    return list(db.scalars(select(sub.c.m).distinct().order_by(sub.c.m.desc())).all())


# ---------------------------------------------------------------------------
# Scoring runner: turns closed weeks/months into ledger entries
# ---------------------------------------------------------------------------


def _today() -> dt.date:
    return dt.date.today()


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
            minutes, calories = days.get(u.id, {}).get(d, (0, 0))
            cal_pts, workout_pts = scoring.score_day(minutes, calories, book.at(u.id, d))
            _add_earn(db, u.id, period, "calorie_daily", cal_pts)
            _add_earn(db, u.id, period, "workout_day", workout_pts)


def _score_week(db: Session, users, book: GoalBook, monday: dt.date, prev_streak: int) -> int:
    """Write one week's ledger rows and its scoring_periods marker. Returns the
    pair's new streak."""
    period = f"wk-{monday.isoformat()}"
    days = _load_days(db, monday, monday + dt.timedelta(days=6))
    weeks = {
        u.id: scoring.score_player_week(days.get(u.id, {}), monday, lambda d, uid=u.id: book.at(uid, d))
        for u in users
    }
    pair_ok = all(w.ok for w in weeks.values())
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
            _add_earn(db, u.id, period, category, owed, note=note)
        extra = scoring.scaled(w.subtotal, mult) - w.subtotal
        weeks_word = "Week" if streak == 1 else "Weeks"
        _add_earn(
            db, u.id, period, "multiplier", extra,
            note=f"x{mult} ({streak} {weeks_word} in a Row)",
        )

    if pair_ok:
        total = scoring.scaled(scoring.TEAM_BONUS, mult)
        share, rem = divmod(total, len(users))
        for i, u in enumerate(users):
            _add_earn(db, u.id, period, "team_bonus", share + (1 if i < rem else 0))

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
        # each day is judged against the calorie goal in force that day
        hit = sum(
            1
            for day, (_, cal) in days.get(u.id, {}).items()
            if cal >= book.at(u.id, day).min_calories
        )
        _add_earn(
            db, u.id, month, "calorie_monthly", scoring.monthly_points(hit, n),
            note=f"{hit} of {n} Days ({round(hit * 100 / n)}%)",
        )
    db.add(ScoringPeriod(period=month, kind="month"))


def run_scoring(db: Session, today: dt.date | None = None) -> list[str]:
    """Score every closed week/month that has not been scored yet, in order.
    Safe to call any time and as often as you like: scored periods are skipped,
    so it is idempotent. Returns the periods scored by this call."""
    if not SCORING_ENABLED:
        return []
    today = today or _today()
    _lock_ledger(db)
    users = db.execute(select(User).order_by(User.name)).scalars().all()
    if not users:
        db.rollback()
        return []
    book = load_goal_book(db)
    done = {
        p.period: p for p in db.scalars(select(ScoringPeriod))
    }
    scored: list[str] = []

    # Weeks run Monday to Sunday; the first one is the first full week on or
    # after the scoring start. Replayed in order so streaks carry correctly.
    monday = SCORING_START + dt.timedelta(days=(7 - SCORING_START.weekday()) % 7)
    streak = 0
    while scoring.week_ready_date(monday) <= today:
        period = f"wk-{monday.isoformat()}"
        if period in done:
            streak = done[period].streak or 0
        else:
            if not _day_synced(db, users, monday + dt.timedelta(days=6), today):
                break  # later weeks depend on this one's streak, so wait
            streak = _score_week(db, users, book, monday, streak)
            scored.append(period)
        monday += dt.timedelta(days=7)

    # `monday` is now the first week that is not scored yet (still open, or
    # waiting on a sync). Its settled days are paid daily in the meantime.
    _pay_settled_days(db, users, book, monday, today)

    # Calendar months that start on/after the scoring start and have finished.
    y, m = SCORING_START.year, SCORING_START.month
    if SCORING_START.day != 1:
        y, m = y + (m == 12), (m % 12) + 1
    while True:
        month = f"{y}-{m:02d}"
        _, last = scoring.month_bounds(month)
        if last + dt.timedelta(days=1 + scoring.GRACE_DAYS) > today:
            break
        if month not in done and _day_synced(db, users, last, today):
            _score_month(db, users, book, month)
            scored.append(month)
        y, m = y + (m == 12), (m % 12) + 1

    db.commit()
    return scored


@app.post("/api/scoring/run", dependencies=[Depends(current_user)])
def scoring_run(db: Session = Depends(get_session)):
    return {
        "enabled": SCORING_ENABLED,
        "scored": run_scoring(db),
        "scoring_start": SCORING_START.isoformat(),
    }
