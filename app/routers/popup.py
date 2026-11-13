"""The points popup: what's new for you since you last closed it."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import popup
from app.auth import current_user
from app.db import get_session
from app.models import User

router = APIRouter()


class SeenIn(BaseModel):
    ledger_id: int | None = None
    my_week: str | None = None
    team_week: str | None = None


@router.get("/api/popup")
def get_popup(user: User = Depends(current_user), db: Session = Depends(get_session)):
    """`popup` is null when there is nothing new to show. Send `ack` back to
    POST /api/popup/seen once the popup is closed."""
    return popup.build(db, user)


@router.post("/api/popup/seen", status_code=204)
def popup_seen(body: SeenIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    popup.mark_seen(db, user, body.ledger_id, body.my_week, body.team_week)
