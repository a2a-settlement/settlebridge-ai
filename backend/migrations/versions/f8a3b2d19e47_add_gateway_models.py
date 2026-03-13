"""add gateway models

Revision ID: f8a3b2d19e47
Revises: c4a7f1e89b30
Create Date: 2026-03-13 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f8a3b2d19e47"
down_revision: Union[str, None] = "c4a7f1e89b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

policydecisiontype_enum = postgresql.ENUM(
    "approve", "block", "flag", name="policydecisiontype", create_type=False
)
alertconditiontype_enum = postgresql.ENUM(
    "reputation_below", "spending_approaching", "error_rate_above",
    "anomalous_volume", "policy_violation_spike",
    name="alertconditiontype", create_type=False,
)
alertchannel_enum = postgresql.ENUM(
    "dashboard", "webhook", "email", name="alertchannel", create_type=False
)


def upgrade() -> None:
    policydecisiontype_enum.create(op.get_bind(), checkfirst=True)
    alertconditiontype_enum.create(op.get_bind(), checkfirst=True)
    alertchannel_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "trust_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("source_agent", sa.String(255), nullable=False),
        sa.Column("target_agent", sa.String(255), nullable=False),
        sa.Column("policy_decision", policydecisiontype_enum, nullable=False),
        sa.Column("escrow_id", sa.String(255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("merkle_root", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_entries_timestamp", "audit_entries", ["timestamp"])
    op.create_index("ix_audit_entries_source_agent", "audit_entries", ["source_agent"])
    op.create_index("ix_audit_entries_target_agent", "audit_entries", ["target_agent"])

    op.create_table(
        "reputation_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("reputation_score", sa.Float(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reputation_snapshots_agent_id", "reputation_snapshots", ["agent_id"])
    op.create_index("ix_reputation_snapshots_snapshot_at", "reputation_snapshots", ["snapshot_at"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("condition_type", alertconditiontype_enum, nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("channel", alertchannel_enum, nullable=False, server_default="dashboard"),
        sa.Column("agent_filter", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_agent_id", "alert_events", ["agent_id"])


def downgrade() -> None:
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
    op.drop_table("reputation_snapshots")
    op.drop_table("audit_entries")
    op.drop_table("trust_policies")
    alertchannel_enum.drop(op.get_bind(), checkfirst=True)
    alertconditiontype_enum.drop(op.get_bind(), checkfirst=True)
    policydecisiontype_enum.drop(op.get_bind(), checkfirst=True)
