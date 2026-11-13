"""Your API keys: list them and revoke one (lost phone)."""
import datetime as dt

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, hash_key
from app.db import get_session
from app.models import ApiKey, User

router = APIRouter()


@router.get("/api/keys")
def list_keys(
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
    x_api_key: str | None = Header(default=None),
):
    """Your keys, newest first. Only the label and dates are shown (the key itself
    is never stored). `current` marks the key making this request."""
    rows = db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    ).all()
    mine = hash_key(x_api_key or "")
    return [
        {
            "id": k.id,
            "label": k.label,
            "created_at": k.created_at.isoformat(),
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "current": k.key_hash == mine,
        }
        for k in rows
    ]


@router.delete("/api/keys/{key_id}")
def revoke_key(
    key_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
    x_api_key: str | None = Header(default=None),
):
    """Revoke one of your own keys. The key you're using right now can't be
    revoked here, so you can't lock yourself out by accident."""
    key = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    if key is None:
        raise HTTPException(status_code=404, detail="no such key")
    if key.key_hash == hash_key(x_api_key or ""):
        raise HTTPException(status_code=409, detail="that's the key you're using right now")
    if key.revoked_at is None:
        key.revoked_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    return {"id": key.id, "revoked_at": key.revoked_at.isoformat()}
