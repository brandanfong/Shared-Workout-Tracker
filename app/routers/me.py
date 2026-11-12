"""The signed-in person: their goals and their color."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import colors, config, scoring
from app.auth import current_user
from app.db import get_session
from app.goals import DEFAULT_GOALS, goal_dict, load_goal_book
from app.models import User, UserGoal
from app.schemas import ColorIn, SettingsIn
from app.users import effective_colors

router = APIRouter()


@router.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)):
    """Your goals as they stand today, and whether scoring is switched on."""
    book = load_goal_book(db)
    return {
        "name": user.name,
        **goal_dict(book.at(user.id, config.today())),
        # False until they save goals (or pick the defaults) for the first time.
        "goals_set": book.latest(user.id) is not None,
        "scoring_enabled": config.SCORING_ENABLED,
        "color": effective_colors(db)[user.name],
    }


@router.patch("/api/me/settings")
def update_my_settings(
    body: SettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    """Saves goals. Minutes and calories apply from today; each day is judged
    by the goals in force that day. The weekly workout target counts for the
    whole week you save it in and locks when that week ends. Saving again the
    same day replaces that day's entry. Your first save is when you start
    playing: days before it are not judged or scored."""
    today = config.today()
    book = load_goal_book(db)
    base = book.latest(user.id) or DEFAULT_GOALS
    effective_from = today

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


@router.get("/api/colors")
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


@router.patch("/api/me/color")
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
