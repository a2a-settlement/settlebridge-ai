"""finding embargo state and fix-verifier opt-in

Revision ID: c8f1a2e4b6d0
Revises: a7c3e9f1b2d4
Create Date: 2026-11-10 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8f1a2e4b6d0"
down_revision: Union[str, None] = "a7c3e9f1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

embargostate = postgresql.ENUM(
    "private",
    "notified",
    "embargoed",
    "disclosed",
    name="embargostate",
    create_type=False,
)


def upgrade() -> None:
    embargostate.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "findings",
        sa.Column(
            "embargo_state",
            embargostate,
            nullable=False,
            server_default=sa.text("'private'"),
        ),
    )
    op.add_column(
        "findings",
        sa.Column("embargo_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "github_repo_links",
        sa.Column(
            "fix_verifier_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("github_repo_links", "fix_verifier_opt_in")
    op.drop_column("findings", "embargo_until")
    op.drop_column("findings", "embargo_state")
    embargostate.drop(op.get_bind(), checkfirst=True)
