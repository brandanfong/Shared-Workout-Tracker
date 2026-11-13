"""The shared points balance, its history, manual adjustments, and running scoring."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app import config, labels
from app.auth import current_user
from app.db import get_session
from app.ledger import append, current_balance, entry_json, lock_ledger
from app.models import LedgerEntry, Reward, User
from app.schemas import AdjustIn
from app.scoring_runner import run_scoring

router = APIRouter()


@router.get("/api/balance", dependencies=[Depends(current_user)])
def balance(db: Session = Depends(get_session)):
    per_person = db.execute(
        select(User.name, func.coalesce(func.sum(LedgerEntry.points), 0).label("pts"))
        .join(LedgerEntry, LedgerEntry.user_id == User.id)
        .where(LedgerEntry.type == "earn")
        .group_by(User.name)
        .order_by(User.name)
    ).all()
    return {
        "balance": current_balance(db),
        "earned_by": {r.name: int(r.pts) for r in per_person},
    }


@router.post("/api/adjust", status_code=201)
def adjust(
    body: AdjustIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    """Fix a mistake with a new row (never by editing history)."""
    lock_ledger(db)
    if body.points == 0 or current_balance(db) + body.points < 0:
        raise HTTPException(status_code=409, detail="balance cannot go below zero")
    entry = append(
        db, type="adjust", user_id=user.id, category="adjust",
        points=body.points, note=body.note,
    )
    db.commit()
    return entry_json(entry, user.name, None)


def _earned_month():
    """The YYYY-MM an entry belongs to: the month it was earned FOR (read from its
    period, so a day paid a day late still counts in its own month), or for
    redemptions and adjustments the month they happened."""
    p = LedgerEntry.period
    return case(
        (or_(p.like("day-%"), p.like("wk-%")), func.substr(p, func.strpos(p, "-") + 1, 7)),
        (p.op("~")(r"^\d{4}-\d{2}$"), p),
        else_=func.to_char(func.timezone(config.GROUP_TIMEZONE, LedgerEntry.created_at), "YYYY-MM"),
    )


def _like_pattern(text_: str) -> str:
    """Turn what someone typed into a safe 'contains' pattern."""
    escaped = text_.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _searchable_text():
    """One string per row holding what the history table shows (who, the sentence
    for what happened, the reward, the note), so searching finds what you can
    see. Dates also match as month names ("november") and short dates ("nov 4")."""
    p = LedgerEntry.period
    category_label = case(
        *[(LedgerEntry.category == k, v) for k, v in labels.CATEGORY_LABELS.items()],
        else_=func.replace(LedgerEntry.category, "_", " "),
    )
    type_label = case(
        (LedgerEntry.type == "earn", "earned"),
        (LedgerEntry.type == "redeem", "redeemed"),
        else_="adjustment",
    )
    period_label = case(
        (p.like("wk-%"), func.concat("week of ", func.substr(p, 4))),
        (p.like("day-%"), func.substr(p, 5)),
        else_=func.coalesce(p, ""),
    )
    day_words = case(
        (
            or_(p.like("day-%"), p.like("wk-%")),
            func.to_char(
                func.to_date(func.substr(p, func.strpos(p, "-") + 1, 10), "YYYY-MM-DD"),
                "FMMon FMDD FMMonth YYYY FMMon FMDD, YYYY",
            ),
        ),
        (
            p.op("~")(r"^\d{4}-\d{2}$"),
            func.to_char(func.to_date(func.concat(p, "-01"), "YYYY-MM-DD"), "FMMon FMMonth YYYY"),
        ),
        else_="",
    )
    return func.concat_ws(
        " ",
        User.name,
        type_label,
        category_label,
        period_label,
        day_words,
        func.coalesce(Reward.name, ""),
        func.coalesce(LedgerEntry.note, ""),
    )


@router.get("/api/ledger", dependencies=[Depends(current_user)])
def ledger(
    player: str | None = None,
    type: str | None = Query(None, pattern="^(earn|redeem|adjust)$"),
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    q: str | None = Query(None, max_length=100),
    before: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_session),
):
    """The points history, newest first. Filters combine. `before` is the id of
    the oldest entry you already have, to fetch the next page."""
    stmt = (
        select(LedgerEntry, User.name, Reward.name)
        .join(User, User.id == LedgerEntry.user_id)
        .outerjoin(Reward, Reward.id == LedgerEntry.reward_id)
        .order_by(LedgerEntry.id.desc())
        .limit(limit)
    )
    if player:
        stmt = stmt.where(User.name == player.strip().lower())
    if type:
        stmt = stmt.where(LedgerEntry.type == type)
    if month:
        stmt = stmt.where(_earned_month() == month)
    if q and q.strip():
        stmt = stmt.where(_searchable_text().ilike(_like_pattern(q), escape="\\"))
    if before:
        stmt = stmt.where(LedgerEntry.id < before)
    return [entry_json(e, p, rw) for e, p, rw in db.execute(stmt).all()]


@router.get("/api/points-history", dependencies=[Depends(current_user)])
def points_history(db: Session = Depends(get_session)):
    """Data for the two history charts, oldest first.
    earned:  points earned per person per day. Redemptions and adjustments are
             left out, same as the "Earned by" totals, so those lines only go up.
    balance: the shared balance at the end of each day that had any activity,
             so it moves down when rewards are redeemed."""
    day = func.date(func.timezone(config.GROUP_TIMEZONE, LedgerEntry.created_at))
    earned = db.execute(
        select(User.name, day.label("d"), func.sum(LedgerEntry.points).label("pts"))
        .join(User, User.id == LedgerEntry.user_id)
        .where(LedgerEntry.type == "earn")
        .group_by(User.name, day)
        .order_by(day, User.name)
    ).all()
    balance = db.execute(
        select(day.label("d"), LedgerEntry.balance_after)
        .ext(distinct_on(day))
        .order_by(day, LedgerEntry.id.desc())
    ).all()
    return {
        "earned": [{"name": r.name, "date": r.d.isoformat(), "points": int(r.pts)} for r in earned],
        "balance": [{"date": r.d.isoformat(), "balance": int(r.balance_after)} for r in balance],
    }


@router.get("/api/ledger/months", dependencies=[Depends(current_user)])
def ledger_months(db: Session = Depends(get_session)):
    """Months that have any history, newest first, for the month filter."""
    sub = select(_earned_month().label("m")).subquery()
    return list(db.scalars(select(sub.c.m).distinct().order_by(sub.c.m.desc())).all())


@router.post("/api/scoring/run", dependencies=[Depends(current_user)])
def scoring_run(db: Session = Depends(get_session)):
    return {
        "enabled": config.SCORING_ENABLED,
        "scored": run_scoring(db),
        "scoring_start": config.SCORING_START.isoformat(),
    }
