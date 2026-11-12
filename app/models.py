"""Database tables."""
import datetime as dt

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey,
                        Index, Integer, String, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# effective_from used for goals that apply to all of a person's history.
EPOCH = dt.date(1970, 1, 1)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    # Chosen display color (#rrggbb). None means "automatic".
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    # The automatic color, given once when the person is created and never
    # changed, so adding someone later can't shuffle everyone else's color.
    auto_color: Mapped[str | None] = mapped_column(String, nullable=True)


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


class UserGoal(Base):
    """History of each person's goals. A person's goals on any day are the row
    with the latest effective_from that is on or before that day. Minutes and
    calories are read for the exact day being judged; the weekly workout target
    is read for the Sunday the week ended. People with no rows use the
    defaults in config."""

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
