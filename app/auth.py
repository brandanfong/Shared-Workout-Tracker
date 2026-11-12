"""API keys: creating them and checking the X-API-Key header."""
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, User
from app.users import next_free_color


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
        user = User(name=name, auto_color=next_free_color(db))
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
