"""repo visibility, escrow cancel secrets, account kind

Revision ID: e3f7a9b1c2d4
Revises: d2e6f4a5b7c8
Create Date: 2026-11-06 17:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3f7a9b1c2d4"
down_revision: Union[str, None] = "d2e6f4a5b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("github_repo_links", sa.Column("visibility", sa.String(16), nullable=True))
    op.add_column("github_leases", sa.Column("hold_cancel_secret", sa.String(255), nullable=True))
    op.add_column("github_settlements", sa.Column("cancel_secret", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("account_kind", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "account_kind")
    op.drop_column("github_settlements", "cancel_secret")
    op.drop_column("github_leases", "hold_cancel_secret")
    op.drop_column("github_repo_links", "visibility")
