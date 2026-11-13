"""The current week's live progress, for the "This week" panel."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import User
from app.week_progress import week_progress

router = APIRouter()


@router.get("/api/week")
def this_week(user: User = Depends(current_user), db: Session = Depends(get_session)):
    """Your own calorie days and workout days so far this week (Monday to Sunday,
    the group's "today"), how each is pacing, and the team streak. Nothing about
    teammates. A preview only: nothing is paid until the week is scored."""
    return week_progress(db, user)
