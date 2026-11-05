"""Markdown templates for SettleBridge GitHub bot comments. Pure functions."""

from __future__ import annotations

import uuid


def bounty_marker(bounty_id: uuid.UUID | str) -> str:
    raise NotImplementedError


def render_advertised(
    *,
    bounty_id: uuid.UUID | str,
    amount_ate: int,
    amount_usd: str,
    spec_version: int,
    spec_hash: str,
    required_kya: int,
    required_ema: float,
    required_checks: list[dict],
    test_command: str | None,
    market_url: str,
) -> str:
    raise NotImplementedError


def render_not_advertised(*, reason: str) -> str:
    raise NotImplementedError


def render_evaluation(
    *,
    bounty_id: uuid.UUID | str,
    outcome: str,
    head_sha: str,
    spec_version: int,
    findings: list[dict],
    failed_checks: list[str] | None = None,
    reviewer_summary: str | None = None,
) -> str:
    raise NotImplementedError


def render_accepted_payment_pending(
    *, bounty_id: uuid.UUID | str, head_sha: str, reason: str | None = None
) -> str:
    raise NotImplementedError


def render_paid(
    *, bounty_id: uuid.UUID | str, escrow_id: str, amount_ate: int, fee_ate: int
) -> str:
    raise NotImplementedError


def render_cancelled(*, bounty_id: uuid.UUID | str, reason: str) -> str:
    raise NotImplementedError
