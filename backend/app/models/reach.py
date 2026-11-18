"""Offline CVE reach rankings imported from a settlebridge-verifier JSONL bundle.

Signal columns stay NULL when the bundle omits them. They are never stored as 0.
``imported_at`` is set by the database, not by the bundle.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    Index,
    String,
    desc,
    func,
    nulls_last,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PackageReach(Base):
    """Download, dependent, and exposure signals for one package."""

    __tablename__ = "package_reach"

    ecosystem: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    downloads_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloads_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dependents_direct: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dependents_indirect: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dependent_repos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    docker_downloads: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    popcon_inst: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    exposed_instances: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloads_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dependents_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ecosystems_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    popcon_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exposure_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    errors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CvePriority(Base):
    """One CVE's reach ranking for a population. ``priority`` NULL sorts last."""

    __tablename__ = "cve_priority"
    __table_args__ = (Index("ix_cve_priority_priority", nulls_last(desc("priority"))),)

    cve: Mapped[str] = mapped_column(String(32), primary_key=True)
    population: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ecosystem: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    downloads_pct: Mapped[float | None] = mapped_column(Double, nullable=True)
    dependents_pct: Mapped[float | None] = mapped_column(Double, nullable=True)
    docker_pct: Mapped[float | None] = mapped_column(Double, nullable=True)
    reach_score: Mapped[float | None] = mapped_column(Double, nullable=True)
    epss_percentile: Mapped[float | None] = mapped_column(Double, nullable=True)
    threat_score: Mapped[float | None] = mapped_column(Double, nullable=True)
    priority: Mapped[float | None] = mapped_column(Double, nullable=True)
    kev: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # fixed_later, possibly_open, or model_only; NULL for backlog rows.
    fix_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    weights_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
