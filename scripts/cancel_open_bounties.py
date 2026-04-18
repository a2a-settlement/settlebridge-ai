#!/usr/bin/env python3
"""Cancel OPEN and DRAFT bounties using each requester's linked exchange API key.
Run from repo root with DATABASE_URL in .env. Uses public marketplace API."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

BASE = os.environ.get("MARKETPLACE_API", "https://market.settlebridge.ai/api")


def load_db_url() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text().splitlines():
        if line.strip().startswith("DATABASE_URL="):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            return v.replace("postgresql+asyncpg://", "postgresql://")
    raise SystemExit("DATABASE_URL not found in .env")


def fetch_rows(db_url: str) -> list[tuple[str, str, str, str | None]]:
    """bounty_id, status, email, exchange_api_key"""
    q = """
    SELECT b.id::text, b.status::text, u.email, u.exchange_api_key
    FROM bounties b
    JOIN users u ON u.id = b.requester_id
    WHERE b.status::text IN ('OPEN', 'DRAFT')
    ORDER BY b.created_at;
    """
    r = subprocess.run(
        ["psql", db_url, "-t", "-A", "-F", "|", "-c", q],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit(1)
    rows: list[tuple[str, str, str, str | None]] = []
    for line in r.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        bid, st, email, key = parts[0], parts[1], parts[2], parts[3] or None
        if key == "":
            key = None
        rows.append((bid, st, email, key))
    return rows


def cancel_one(api_key: str, bounty_id: str) -> tuple[bool, str]:
    r = requests.post(
        f"{BASE}/auth/exchange-login",
        json={"api_key": api_key},
        timeout=60,
    )
    if r.status_code != 200:
        return False, f"exchange-login {r.status_code}: {r.text[:200]}"
    token = r.json()["access_token"]
    cr = requests.post(
        f"{BASE}/bounties/{bounty_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    if cr.status_code != 200:
        return False, f"cancel {cr.status_code}: {cr.text[:300]}"
    return True, "ok"


def main() -> None:
    db_url = load_db_url()
    rows = fetch_rows(db_url)
    print(f"Found {len(rows)} OPEN/DRAFT bounties")

    ok_n = 0
    fail_n = 0
    for bounty_id, st, email, api_key in rows:
        if not api_key:
            print(f"SKIP (no exchange_api_key) {bounty_id} {st} {email}")
            fail_n += 1
            continue
        ok, msg = cancel_one(api_key, bounty_id)
        if ok:
            print(f"OK {bounty_id} ({st}) {email}")
            ok_n += 1
        else:
            print(f"FAIL {bounty_id} {email}: {msg}", file=sys.stderr)
            fail_n += 1
        time.sleep(0.15)

    print(f"Done: {ok_n} cancelled, {fail_n} skipped/failed")


if __name__ == "__main__":
    main()
