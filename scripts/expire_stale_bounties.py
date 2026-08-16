#!/usr/bin/env python3
"""Expire stale IN_REVIEW bounties whose exchange escrow is already terminal.

Selection: bounties in IN_REVIEW created before --before (default 2026-06-01)
whose exchange escrow status is expired or refunded. Does NOT touch
partially_released holdbacks (e.g. today's morning-signal smoke test).

Default is dry-run. Pass --apply to write.

Run from repo root with DATABASE_URL in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

EXCHANGE = os.environ.get("EXCHANGE_URL", "https://exchange.a2a-settlement.org")
TERMINAL = frozenset({"expired", "refunded"})
NOTES = (
    "Escrow expired/refunded on exchange before requester review; "
    "marked expired by expire_stale_bounties.py"
)


def load_db_url() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text().splitlines():
        if line.strip().startswith("DATABASE_URL="):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            return v.replace("postgresql+asyncpg://", "postgresql://")
    raise SystemExit("DATABASE_URL not found in .env")


def load_requester_key() -> str:
    """Any valid exchange key can read public escrow status; prefer requester."""
    for path in (
        Path("/root/.hermes/clawd/.exchange-credentials.json"),
        Path.home() / ".hermes/clawd/.exchange-credentials.json",
    ):
        if path.exists():
            data = json.loads(path.read_text())
            key = (data.get("requester") or {}).get("api_key")
            if key:
                return key
    raise SystemExit("No exchange requester api_key found in credentials file")


def psql(db_url: str, sql: str) -> str:
    r = subprocess.run(
        ["psql", db_url, "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit(1)
    return r.stdout


def fetch_candidates(db_url: str, before: str) -> list[tuple[str, str, str, str]]:
    """Return (bounty_id, title, created_at, escrow_id) for IN_REVIEW before cutoff."""
    q = f"""
    SELECT b.id::text, b.title, b.created_at::text, COALESCE(b.escrow_id, '')
    FROM bounties b
    WHERE b.status::text = 'IN_REVIEW'
      AND b.created_at < '{before}'::timestamptz
    ORDER BY b.created_at;
    """
    rows: list[tuple[str, str, str, str]] = []
    for line in psql(db_url, q).strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def escrow_status(api_key: str, escrow_id: str) -> str:
    if not escrow_id or escrow_id == "pending_claim":
        return "none"
    req = urllib.request.Request(
        f"{EXCHANGE}/v1/exchange/escrows/{escrow_id}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        return str(data.get("status") or "unknown")
    except Exception as exc:
        return f"error:{exc}"


def expire_one(db_url: str, bounty_id: str) -> None:
    """Mark bounty EXPIRED and close pending submissions/claims in one transaction."""
    sql = f"""
    BEGIN;
    UPDATE bounties
       SET status = 'EXPIRED'::bountystatus,
           escrow_id = NULL
     WHERE id = '{bounty_id}'::uuid
       AND status::text = 'IN_REVIEW';

    UPDATE submissions
       SET status = 'REJECTED'::submissionstatus,
           reviewer_notes = '{NOTES}',
           reviewed_at = NOW()
     WHERE bounty_id = '{bounty_id}'::uuid
       AND status::text = 'PENDING_REVIEW';

    UPDATE claims
       SET status = 'REJECTED'::claimstatus,
           resolved_at = NOW()
     WHERE bounty_id = '{bounty_id}'::uuid
       AND status::text IN ('ACTIVE', 'SUBMITTED');

    COMMIT;
    """
    # Use unaligned multi-statement via psql -c; wrap errors.
    r = subprocess.run(
        ["psql", db_url, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or "psql failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        default="2026-06-01",
        help="Only consider IN_REVIEW bounties created before this date (UTC)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    args = parser.parse_args()

    db_url = load_db_url()
    api_key = load_requester_key()
    candidates = fetch_candidates(db_url, args.before)
    print(f"Found {len(candidates)} IN_REVIEW bounties created before {args.before}")

    matched: list[tuple[str, str, str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    for bid, title, created, escrow_id in candidates:
        st = escrow_status(api_key, escrow_id)
        if st in TERMINAL:
            matched.append((bid, title, created, escrow_id, st))
        else:
            skipped.append((bid, title[:40], st))

    print(f"\nTerminal escrow (will expire): {len(matched)}")
    for bid, title, created, escrow_id, st in matched:
        print(f"  {created[:10]}  {bid}  escrow={st}  {title[:50]}")

    if skipped:
        print(f"\nSkipped (escrow not terminal): {len(skipped)}")
        for bid, title, st in skipped:
            print(f"  {bid}  escrow={st}  {title}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
        return

    ok_n = 0
    fail_n = 0
    for bid, title, created, escrow_id, st in matched:
        try:
            expire_one(db_url, bid)
            print(f"OK expired {bid} ({st}) {title[:40]}")
            ok_n += 1
        except Exception as exc:
            print(f"FAIL {bid}: {exc}", file=sys.stderr)
            fail_n += 1

    print(f"\nDone: {ok_n} expired, {fail_n} failed")


if __name__ == "__main__":
    main()
