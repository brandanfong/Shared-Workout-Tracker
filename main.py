import datetime as dt
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (Date, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine, func, select)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# Neon gives postgresql://...; SQLAlchemy needs postgresql+psycopg://...
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
API_KEY = os.environ["API_KEY"]


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


app = FastAPI(title="Shared Workout Tracker")
Base.metadata.create_all(engine)  # swap for Alembic migrations later


def get_session():
    with Session(engine) as session:
        yield session


def require_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="bad key")


@app.get("/")
def root():
    return {"service": "workout-tracker", "docs": "/docs"}


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