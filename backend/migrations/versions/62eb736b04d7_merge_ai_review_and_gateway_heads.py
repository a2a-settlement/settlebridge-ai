"""merge ai_review and gateway heads

Revision ID: 62eb736b04d7
Revises: b94e655e2feb, f8a3b2d19e47
Create Date: 2026-03-13 14:49:33.665686
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '62eb736b04d7'
down_revision: Union[str, None] = ('b94e655e2feb', 'f8a3b2d19e47')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
