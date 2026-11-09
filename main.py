import datetime as dt
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

API_KEY = os.environ["API_KEY"]
STATIC_DIR = Path(__file__).parent / "static"

# A "workout day" needs BOTH thresholds met. Change via env vars, no code edit.
MIN_MINUTES = int(os.getenv("MIN_WORKOUT_MINUTES", "30"))
MIN_CALORIES = int(os.getenv("MIN_WORKOUT_CALORIES", "300"))


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


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
    # JSON keys stay the same as the Shortcut sends them
    name: str = Field(min_length=1, max_length=50)
    date: dt.date
    minutes: int = Field(ge=0, le=1440)
    calories: int = Field(ge=0, le=20000)

    @field_validator("name")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("minutes", "calories", mode="before")
    @classmethod
    def round_to_int(cls, v):
        try:
            return round(float(v))
        except (TypeError, ValueError):
            raise ValueError("must be a number")


app = FastAPI(title="Couples Workout Tracker")
Base.metadata.create_all(engine)  # swap for Alembic migrations later


def get_session():
    with Session(engine) as session:
        yield session


def require_key(x_api_key: str | None = Header(default=None)):
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode(), API_KEY.encode()
    ):
        raise HTTPException(status_code=401, detail="bad key")


@app.get("/")
def dashboard():
    # The page itself contains no secrets; it asks for the key and sends it
    # with each request to the /api endpoints.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/log", dependencies=[Depends(require_key)])
def log(body: LogIn, db: Session = Depends(get_session)):
    # get or create user
    user = db.scalar(select(User).where(User.name == body.name))
    if user is None:
        user = User(name=body.name)
        db.add(user)
        db.flush()

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
    return {"status": "ok", "name": body.name, "date": body.date}


@app.get("/api/activity", dependencies=[Depends(require_key)])
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


@app.get("/api/calendar", dependencies=[Depends(require_key)])
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

    days: dict[str, dict] = {}
    for r in rows:
        days.setdefault(r.date.isoformat(), {})[r.name] = {
            "exercise_minutes": r.exercise_minutes,
            "active_calories": r.active_calories,
            "workout": r.exercise_minutes >= MIN_MINUTES
            and r.active_calories >= MIN_CALORIES,
        }

    return {
        "month": month,
        "users": users,
        "min_minutes": MIN_MINUTES,
        "min_calories": MIN_CALORIES,
        "days": days,
    }