"""merge training loop heads

Revision ID: b1d4e7f20c93
Revises: a3f8c2e90d15, e5f2a1c38d90
Create Date: 2026-04-03

Merges the two training-loop branch heads so Alembic has a single head.
e9f2a4b76c1d already applied the training schema; e5f2a1c38d90 is a
parallel branch that covers the same tables. This merge only unifies
the revision graph — no DDL changes.
"""
from alembic import op

revision = "b1d4e7f20c93"
down_revision = ("a3f8c2e90d15", "e5f2a1c38d90")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
