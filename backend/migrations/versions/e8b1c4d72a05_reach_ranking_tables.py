"""reach ranking tables: package reach signals and CVE priority

Revision ID: e8b1c4d72a05
Revises: d2e5f7a9c1b3
Create Date: 2026-11-17 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e8b1c4d72a05"
down_revision: Union[str, None] = "d2e5f7a9c1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column:
    kwargs = {"server_default": sa.func.now()} if now else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kwargs)


def _bigint(name: str) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=True)


def _float(name: str) -> sa.Column:
    return sa.Column(name, sa.Double(), nullable=True)


def upgrade() -> None:
    op.create_table(
        "package_reach",
        sa.Column("ecosystem", sa.String(32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), primary_key=True, nullable=False),
        _bigint("downloads_30d"),
        _bigint("downloads_total"),
        _bigint("dependents_direct"),
        _bigint("dependents_indirect"),
        _bigint("dependent_repos"),
        _bigint("docker_downloads"),
        _bigint("popcon_inst"),
        _bigint("exposed_instances"),
        _ts("downloads_fetched_at"),
        _ts("dependents_fetched_at"),
        _ts("ecosystems_fetched_at"),
        _ts("popcon_fetched_at"),
        _ts("exposure_fetched_at"),
        sa.Column(
            "errors",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts("imported_at", nullable=False, now=True),
    )
    op.create_table(
        "cve_priority",
        sa.Column("cve", sa.String(32), primary_key=True, nullable=False),
        sa.Column("population", sa.String(16), nullable=False),
        sa.Column("confirmed_source", sa.String(64), nullable=True),
        sa.Column("ecosystem", sa.String(32), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        _float("downloads_pct"),
        _float("dependents_pct"),
        _float("docker_pct"),
        _float("reach_score"),
        _float("epss_percentile"),
        _float("threat_score"),
        _float("priority"),
        sa.Column("kev", sa.Boolean(), nullable=True),
        sa.Column("tier", sa.String(8), nullable=True),
        sa.Column("weights_hash", sa.String(32), nullable=False),
        _ts("computed_at", nullable=False),
        _ts("imported_at", nullable=False, now=True),
    )
    op.create_index(
        "ix_cve_priority_priority",
        "cve_priority",
        [sa.text("priority DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_table("cve_priority")
    op.drop_table("package_reach")
