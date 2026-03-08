"""add contract notification types

Revision ID: 2f17560dd61d
Revises: d38eb84ad01f
Create Date: 2026-03-08 17:31:29.263682
"""
from typing import Sequence, Union

from alembic import op

revision: str = '2f17560dd61d'
down_revision: Union[str, None] = 'd38eb84ad01f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_VALUES = [
    "CONTRACT_CREATED",
    "CONTRACT_ACTIVATED",
    "CONTRACT_CANCELLED",
    "SNAPSHOT_DUE",
    "SNAPSHOT_DELIVERED",
    "SNAPSHOT_MISSED",
]


def upgrade() -> None:
    for val in NEW_VALUES:
        op.execute(f"ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    pass
