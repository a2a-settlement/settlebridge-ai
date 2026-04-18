"""backfill public flag for completed training runs

Revision ID: f1a2b3c4d5e6
Revises: b1d4e7f20c93
Create Date: 2026-04-03

Sets public=True and backfills public_title for all existing COMPLETED
training runs so they appear in the public feed on the Bounties page.
Going forward, complete_run() auto-publishes at completion time.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "b1d4e7f20c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
            UPDATE training_runs
            SET
                public = TRUE,
                public_title = COALESCE(
                    NULLIF(public_title, ''),
                    bounty_snapshot->>'title',
                    'Training Run'
                )
            WHERE status = 'COMPLETED'
              AND public = FALSE
        """)
    )


def downgrade() -> None:
    # Cannot safely reverse — we don't know which rows were private before.
    pass
