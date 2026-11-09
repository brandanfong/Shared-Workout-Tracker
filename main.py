import datetime as dt
import hashlib
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (Date, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine, func, select)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


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


class UserSettings(Base):
    """Per-person goals. A row exists only once someone customizes them;
    everyone else uses the defaults above."""

    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    min_minutes: Mapped[int] = mapped_column(Integer)
    min_calories: Mapped[int] = mapped_column(Integer)


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


app = FastAPI(title="Couples Workout Tracker")
Base.metadata.create_all(engine)  # swap for Alembic migrations later


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


def effective_goals(db: Session) -> dict[str, dict]:
    """Goals for every person: their own if set, otherwise the defaults."""
    rows = db.execute(
        select(User.name, UserSettings.min_minutes, UserSettings.min_calories)
        .outerjoin(UserSettings, UserSettings.user_id == User.id)
        .order_by(User.name)
    ).all()
    return {
        r.name: {
            "min_minutes": r.min_minutes if r.min_minutes is not None else DEFAULT_MIN_MINUTES,
            "min_calories": r.min_calories if r.min_calories is not None else DEFAULT_MIN_CALORIES,
        }
        for r in rows
    }


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
    return {"status": "ok", "name": user.name, "date": body.date}


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)):
    return {"name": user.name, **effective_goals(db)[user.name]}


@app.patch("/api/me/settings")
def update_my_settings(
    body: SettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    settings = db.get(UserSettings, user.id)
    if settings is None:
        settings = UserSettings(
            user_id=user.id,
            min_minutes=DEFAULT_MIN_MINUTES,
            min_calories=DEFAULT_MIN_CALORIES,
        )
        db.add(settings)
    if body.min_minutes is not None:
        settings.min_minutes = body.min_minutes
    if body.min_calories is not None:
        settings.min_calories = body.min_calories
    db.commit()
    return {
        "name": user.name,
        "min_minutes": settings.min_minutes,
        "min_calories": settings.min_calories,
    }


@app.get("/api/activity", dependencies=[Depends(current_user)])
def activity(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_session)):
    since = dt.date.today() - dt.timedelta(days=days)
    rows = db.execute(
        select(
            User.name,
            DailyActivity.date,
            DailyActivity.exercise_minutes,
            DailyActivity.active_calories,
        )
        .join(User, User.id == DailyActivity.user_id)
        .where(DailyActivity.date >= since)
        .order_by(DailyActivity.date, User.name)
    ).all()
    return [
        {
            "name": r.name,
            "date": r.date.isoformat(),
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
        }
        for r in rows
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
            User.name,
            DailyActivity.date,
            DailyActivity.exercise_minutes,
            DailyActivity.active_calories,
        )
        .join(User, User.id == DailyActivity.user_id)
        .where(DailyActivity.date >= start, DailyActivity.date < end)
    ).all()

    goals = effective_goals(db)
    days: dict[str, dict] = {}
    for r in rows:
        g = goals[r.name]
        days.setdefault(r.date.isoformat(), {})[r.name] = {
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
            "workout": r.exercise_minutes >= g["min_minutes"]
            and r.active_calories >= g["min_calories"],
        }

    return {
        "month": month,
        "users": users,
        "goals": goals,
        "days": days,
    }