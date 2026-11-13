"""points popup: what each person has already seen

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('seen_ledger_id', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('seen_my_week', sa.String(), nullable=True))
    op.add_column('users', sa.Column('seen_team_week', sa.String(), nullable=True))
    # Everyone already here has "seen" everything paid so far, so the first popup
    # after this deploy shows only new points instead of replaying all history.
    op.execute(
        "UPDATE users SET "
        "seen_ledger_id = (SELECT max(id) FROM ledger), "
        "seen_my_week = (SELECT max(split_part(period, ':', 1)) FROM scoring_periods "
        "  WHERE kind = 'week_user' AND split_part(period, ':', 2) = 'u' || users.id), "
        "seen_team_week = (SELECT max(period) FROM scoring_periods WHERE kind = 'week')"
    )


def downgrade() -> None:
    op.drop_column('users', 'seen_team_week')
    op.drop_column('users', 'seen_my_week')
    op.drop_column('users', 'seen_ledger_id')
