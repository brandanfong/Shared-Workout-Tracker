"""Request bodies (Pydantic models) for the API."""
import datetime as dt

from pydantic import BaseModel, Field, field_validator

COST_MIN = 1
COST_MAX = 5000


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


class ColorIn(BaseModel):
    color: str | None = None  # null goes back to the automatic color


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
