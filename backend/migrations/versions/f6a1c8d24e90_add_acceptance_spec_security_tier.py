"""acceptance spec security tier

Revision ID: f6a1c8d24e90
Revises: e3f7a9b1c2d4
Create Date: 2026-11-06 18:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a1c8d24e90"
down_revision: Union[str, None] = "e3f7a9b1c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_acceptance_specs",
        sa.Column(
            "security_tier",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("github_acceptance_specs", "security_tier")
