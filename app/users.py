"""People's display colors."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import colors
from app.models import User


def effective_colors(db: Session) -> dict[str, str]:
    """Everyone's display color: the one they picked, else an automatic one
    based on their position by name."""
    users = db.execute(select(User.name, User.color, User.auto_color).order_by(User.name)).all()
    palette = colors.DEFAULT_PALETTE
    return {
        u.name: u.color or u.auto_color or palette[i % len(palette)]
        for i, u in enumerate(users)
    }


def next_free_color(db: Session) -> str:
    """First come, first served: the first default/preset color nobody is using
    (or sitting too close to). If every one is taken, wrap around the palette."""
    taken = {f"u{i}": c for i, c in enumerate(effective_colors(db).values())}
    for c in [*colors.DEFAULT_PALETTE, *colors.PRESETS]:
        if colors.check(c, taken) is None:
            return c
    return colors.DEFAULT_PALETTE[len(taken) % len(colors.DEFAULT_PALETTE)]
