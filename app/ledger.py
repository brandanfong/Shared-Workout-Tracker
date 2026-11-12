"""The points ledger: the append-only record of the shared balance."""
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import labels
from app.models import LedgerEntry

LEDGER_LOCK_KEY = 7001  # serializes ledger writes so balance_after stays correct


def lock_ledger(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LEDGER_LOCK_KEY})


def current_balance(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.points), 0))))


def append(
    db: Session,
    *,
    type: str,
    user_id: int,
    category: str,
    points: int,
    period: str | None = None,
    reward_id: int | None = None,
    note: str | None = None,
) -> LedgerEntry:
    """Add one ledger row. Caller must hold the ledger lock and commit."""
    entry = LedgerEntry(
        type=type,
        user_id=user_id,
        period=period,
        category=category,
        points=points,
        balance_after=current_balance(db) + points,
        reward_id=reward_id,
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def add_earn(
    db: Session,
    user_id: int,
    period: str,
    category: str,
    points: int,
    note: str | None = None,
) -> LedgerEntry | None:
    """Add an earn row unless it is empty or already there. Caller holds the
    ledger lock and commits."""
    if points <= 0:
        return None
    exists = db.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.type == "earn",
            LedgerEntry.user_id == user_id,
            LedgerEntry.period == period,
            LedgerEntry.category == category,
        )
    )
    if exists:
        return None
    return append(
        db, type="earn", user_id=user_id, period=period,
        category=category, points=points, note=note,
    )


def record_earn(
    db: Session,
    user_id: int,
    period: str,
    category: str,
    points: int,
    note: str | None = None,
) -> LedgerEntry | None:
    """Credit points once. Idempotent: a second call for the same person +
    period + category is a no-op (returns None)."""
    lock_ledger(db)
    entry = add_earn(db, user_id, period, category, points, note)
    if entry is None:
        db.rollback()  # release the lock
        return None
    db.commit()
    return entry


def entry_json(e: LedgerEntry, player: str, reward: str | None) -> dict:
    return {
        "id": e.id,
        "created_at": e.created_at.isoformat(),
        "type": e.type,
        "player": player,
        "period": e.period,
        "category": e.category,
        "points": e.points,
        "balance_after": e.balance_after,
        "reward_id": e.reward_id,
        "reward": reward,
        "note": e.note,
        "what": labels.entry_what(e.type, e.category, e.period, e.note, reward),
    }
