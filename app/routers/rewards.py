"""The rewards store: list, add, edit, and redeem rewards."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.ledger import append, current_balance, entry_json, lock_ledger
from app.models import LedgerEntry, Reward, User
from app.schemas import RewardIn, RewardUpdate

router = APIRouter()


def _reward_json(r: Reward, adder: str | None, last: dt.datetime | None = None) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cost": r.cost,
        "repeatable": r.repeatable,
        "cooldown_days": r.cooldown_days,
        "active": r.active,
        "added_by": adder,
        "last_redeemed_at": last.isoformat() if last else None,
    }


@router.get("/api/rewards", dependencies=[Depends(current_user)])
def list_rewards(include_inactive: bool = False, db: Session = Depends(get_session)):
    last = (
        select(LedgerEntry.reward_id, func.max(LedgerEntry.created_at).label("at"))
        .where(LedgerEntry.type == "redeem")
        .group_by(LedgerEntry.reward_id)
        .subquery()
    )
    q = (
        select(Reward, User.name, last.c.at)
        .outerjoin(User, User.id == Reward.added_by)
        .outerjoin(last, last.c.reward_id == Reward.id)
        .order_by(Reward.cost, Reward.id)
    )
    if not include_inactive:
        q = q.where(Reward.active.is_(True))
    return [_reward_json(r, adder, at) for r, adder, at in db.execute(q).all()]


@router.post("/api/rewards", status_code=201)
def add_reward(
    body: RewardIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    r = Reward(
        name=body.name.strip(),
        cost=body.cost,
        repeatable=body.repeatable,
        cooldown_days=body.cooldown_days,
        added_by=user.id,
    )
    db.add(r)
    db.commit()
    return _reward_json(r, user.name)


@router.patch("/api/rewards/{reward_id}")
def update_reward(
    reward_id: int,
    body: RewardUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    r = db.get(Reward, reward_id)
    if r is None:
        raise HTTPException(status_code=404, detail="reward not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(r, field, value.strip() if field == "name" else value)
    db.commit()
    adder = db.scalar(select(User.name).where(User.id == r.added_by))
    return _reward_json(r, adder)


@router.post("/api/rewards/{reward_id}/redeem")
def redeem_reward(
    reward_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    lock_ledger(db)  # serialize so two redeems cannot overspend the balance
    reward = db.get(Reward, reward_id)
    if reward is None or not reward.active:
        raise HTTPException(status_code=404, detail="reward not available")

    last = db.scalar(
        select(func.max(LedgerEntry.created_at)).where(
            LedgerEntry.type == "redeem", LedgerEntry.reward_id == reward.id
        )
    )
    if last is not None:
        if not reward.repeatable:
            raise HTTPException(status_code=409, detail="already redeemed (one-time reward)")
        ready = last + dt.timedelta(days=reward.cooldown_days)
        if reward.cooldown_days and dt.datetime.now(dt.timezone.utc) < ready:
            raise HTTPException(
                status_code=409,
                detail=f"on cooldown until {ready.isoformat()}",
            )

    bal = current_balance(db)
    if bal < reward.cost:
        raise HTTPException(
            status_code=409,
            detail=f"not enough points: balance {bal}, cost {reward.cost}",
        )

    entry = append(
        db,
        type="redeem",
        user_id=user.id,
        category="redeem",
        points=-reward.cost,
        reward_id=reward.id,
        note=reward.name,
    )
    db.commit()
    return entry_json(entry, user.name, reward.name)
