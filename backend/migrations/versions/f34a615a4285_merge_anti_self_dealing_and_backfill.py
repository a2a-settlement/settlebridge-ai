"""merge_anti_self_dealing_and_backfill

Revision ID: f34a615a4285
Revises: c9d1e2f3a4b5, f1a2b3c4d5e6
Create Date: 2026-04-18 14:31:39.568779
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f34a615a4285'
down_revision: Union[str, None] = ('c9d1e2f3a4b5', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
