#!/usr/bin/env python3
"""Publish (or refresh) the AlphaSignal-Ensemble Agent Card on the exchange.

Pulls https://crossbearing.ai/.well-known/agent.json, wraps it in the KYA
sandbox envelope required by PUT /v1/accounts/{id}/card, and stores the full
JSON (including A2A url + rich skills) on account 4f72430b.

Usage:
  python3 scripts/publish_alphasignal_agent_card.py
  python3 scripts/publish_alphasignal_agent_card.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EXCHANGE = "https://exchange.a2a-settlement.org"
WELL_KNOWN = "https://crossbearing.ai/.well-known/agent.json"
CREDS_CANDIDATES = (
    Path("/root/.hermes/clawd/.exchange-credentials.json"),
    Path.home() / ".hermes/clawd/.exchange-credentials.json",
    Path("clawd/.exchange-credentials.json"),
)


def load_servicer() -> dict:
    for p in CREDS_CANDIDATES:
        if p.exists():
            data = json.loads(p.read_text())
            s = data.get("servicer") or {}
            if s.get("api_key") and s.get("account_id"):
                return s
    raise SystemExit("servicer credentials not found")


def http_json(url: str, *, method: str = "GET", key: str | None = None, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode())


def build_card(a2a: dict, account_id: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    skills = a2a.get("skills") or []
    return {
        "protocol_version": "2026.1",
        "name": "AlphaSignal-Ensemble-MultiModel-Forecast",
        "id": account_id,
        "description": a2a.get("description")
        or "Multi-model ensemble price forecasts with GARCH-scaled confidence bands.",
        "kya_level": 0,
        "identity": {"type": "exchange_account"},
        "attestations": [],
        "settlement": {
            "supported_methods": ["a2a-settlement", "settlebridge-bounty"],
            "exchange_url": EXCHANGE,
            "token_types": ["ATE"],
            "partial_completion": True,
        },
        "capabilities": {
            "skills": [s["id"] for s in skills if isinstance(s, dict) and s.get("id")],
            "input_formats": ["text", "application/json"],
            "output_formats": ["application/json", "image/png"],
            "rate_limit": {"requests_per_minute": 30, "concurrent_tasks": 3},
        },
        "policies": {
            "data_retention": "Forecast artifacts retained per Crossbearing product policy",
            "pii_handling": "No PII required; ticker symbols only",
            "dispute_resolution": "SettleBridge / A2A-SE escrow dispute flow",
        },
        "metadata": {
            "created": "2026-03-01T00:00:00+00:00",
            "updated": now,
            "well_known_url": WELL_KNOWN,
            "provider_brand": a2a.get("name"),
        },
        "version": a2a.get("version", "3.0"),
        "url": a2a.get("url"),
        "authentication": a2a.get("authentication"),
        "skills": skills,
        "a2a_capabilities": a2a.get("capabilities"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    servicer = load_servicer()
    aid = servicer["account_id"]
    _, a2a = http_json(WELL_KNOWN)
    card = build_card(a2a, aid)
    print(f"account={aid} url={card.get('url')} skills={len(card.get('skills') or [])}")

    if args.dry_run:
        print(json.dumps(card, indent=2)[:1200])
        print("\nDry run only.")
        return

    status, resp = http_json(
        f"{EXCHANGE}/v1/accounts/{aid}/card",
        method="PUT",
        key=servicer["api_key"],
        body=card,
    )
    print(f"PUT {status} kya_level_verified={resp.get('kya_level_verified')}")
    _, got = http_json(f"{EXCHANGE}/v1/accounts/{aid}/card")
    print(f"GET url={(got.get('card') or {}).get('url')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
