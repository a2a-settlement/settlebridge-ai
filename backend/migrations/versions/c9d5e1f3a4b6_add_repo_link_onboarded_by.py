"""add onboarded_by_user_id and onboarded_at to github_repo_links

Revision ID: c9d5e1f3a4b6
Revises: b8c4d0e2f3a5
Create Date: 2026-11-05 19:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c9d5e1f3a4b6"
down_revision: Union[str, None] = "b8c4d0e2f3a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_repo_links",
        sa.Column(
            "onboarded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_github_repo_links_onboarded_by_user_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "github_repo_links",
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_github_repo_links_onboarded_by_user_id", "github_repo_links", type_="foreignkey"
    )
    op.drop_column("github_repo_links", "onboarded_at")
    op.drop_column("github_repo_links", "onboarded_by_user_id")
