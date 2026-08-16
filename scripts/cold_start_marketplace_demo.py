#!/usr/bin/env python3
"""Cold-start marketplace demo: two strangers register, discover, transact.

Buyer and seller register as fresh exchange accounts (no shared account IDs).
Seller publishes an Agent Card. Buyer posts + funds an open bounty. Seller
discovers it via GET /api/bounties?status=open (search by public run marker),
claims, submits a trivial JSON deliverable. Buyer approves after AI review.

Marketplace visibility (watch while it runs):
  Agents:   https://market.settlebridge.ai/agents
  Bounties: https://market.settlebridge.ai/bounties
            Open → In Progress → Completed

Usage:
  python3 scripts/cold_start_marketplace_demo.py --pause 20
  python3 scripts/cold_start_marketplace_demo.py --reward 25 --pause 15
  python3 scripts/cold_start_marketplace_demo.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

EXCHANGE = "https://exchange.a2a-settlement.org"
MARKET = "https://market.settlebridge.ai"
_CTX = ssl.create_default_context()
STATE_DIR = Path("/tmp/settlebridge-cold-start")


def http(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


def pause(sec: float, label: str) -> None:
    if sec <= 0:
        return
    print(f"\n>>> PAUSE {sec:.0f}s — {label}")
    time.sleep(sec)


def register(bot_name: str, developer_id: str, email: str) -> dict:
    st, body = http(
        f"{EXCHANGE}/v1/accounts/register",
        method="POST",
        body={
            "bot_name": bot_name,
            "developer_id": developer_id,
            "developer_name": developer_id,
            "contact_email": email,
            "description": f"Cold-start demo agent ({bot_name})",
            "skills": ["cold-start-demo", "echo-json"],
        },
    )
    if st not in (200, 201) or not isinstance(body, dict):
        raise SystemExit(f"register {bot_name} failed ({st}): {body}")
    # Exchange returns { account: { id, bot_name, ... }, api_key, starter_tokens }
    acct = body.get("account") if isinstance(body.get("account"), dict) else body
    api_key = body.get("api_key") or acct.get("api_key")
    account_id = body.get("account_id") or acct.get("id") or acct.get("account_id")
    if not api_key or not account_id:
        raise SystemExit(f"register missing key/id: {body}")
    return {
        "bot_name": bot_name,
        "account_id": account_id,
        "api_key": api_key,
        "raw": body,
    }


def publish_card(account: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    aid = account["account_id"]
    card = {
        "protocol_version": "2026.1",
        "name": account["bot_name"],
        "id": aid,
        "description": (
            "Cold-start demo seller. Accepts trivial JSON echo bounties and returns "
            '{"ok": true, "echo": <input>}.'
        ),
        "kya_level": 0,
        "identity": {"type": "exchange_account"},
        "attestations": [],
        "settlement": {
            "supported_methods": ["a2a-settlement", "settlebridge-bounty"],
            "exchange_url": EXCHANGE,
            "token_types": ["ATE"],
        },
        "capabilities": {
            "skills": ["echo-json"],
            "input_formats": ["application/json"],
            "output_formats": ["application/json"],
        },
        "policies": {"dispute_resolution": "SettleBridge escrow dispute flow"},
        "metadata": {"created": now, "updated": now},
        "version": "1.0.0",
        # Demo sellers often have no live A2A host; card still documents intent.
        "url": f"{MARKET}/api/agents/{aid}",
        "authentication": {
            "type": "bearer",
            "description": "Marketplace JWT via exchange-login; bounty claim/submit flow",
        },
        "skills": [
            {
                "id": "echo-json",
                "name": "Echo JSON deliverable",
                "description": "Claim open echo bounties and submit valid application/json.",
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
    }
    st, body = http(
        f"{EXCHANGE}/v1/accounts/{aid}/card",
        method="PUT",
        body=card,
        headers={"Authorization": f"Bearer {account['api_key']}"},
    )
    if st != 200:
        raise SystemExit(f"publish card failed ({st}): {body}")


def market_login(api_key: str) -> str:
    st, body = http(
        f"{MARKET}/api/auth/exchange-login",
        method="POST",
        body={"api_key": api_key},
    )
    if st != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise SystemExit(f"exchange-login failed ({st}): {body}")
    return body["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def balance(api_key: str) -> dict:
    st, body = http(
        f"{EXCHANGE}/v1/exchange/balance",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if st != 200 or not isinstance(body, dict):
        raise SystemExit(f"balance failed ({st}): {body}")
    return body


def seller_find_bounty(token: str, marker: str, tries: int = 12) -> dict:
    """Autonomous discovery: browse open bounties; no buyer account_id required."""
    for i in range(tries):
        st, body = http(
            f"{MARKET}/api/bounties?status=open&page=1&page_size=50"
            f"&search={urllib.parse.quote(marker)}",
            headers=bearer(token),
        )
        if st == 200 and isinstance(body, dict):
            for b in body.get("bounties") or []:
                if marker in (b.get("title") or "") and b.get("status") == "open":
                    return b
        time.sleep(2)
    raise SystemExit(f"seller could not discover open bounty with marker {marker}")


def poll_ai(token: str, submission_id: str, timeout_sec: int = 180) -> dict:
    deadline = time.time() + timeout_sec
    last: dict = {}
    while time.time() < deadline:
        st, body = http(
            f"{MARKET}/api/submissions/{submission_id}",
            headers=bearer(token),
        )
        if st == 200 and isinstance(body, dict):
            last = body
            ai = body.get("ai_review") or {}
            if body.get("status") != "pending_review" or ai.get("recommendation") or ai.get("score") is not None:
                return body
        time.sleep(5)
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reward", type=int, default=25, help="ATE locked in escrow")
    parser.add_argument(
        "--pause",
        type=float,
        default=15,
        help="Seconds to pause between phases so you can watch the UI (0=no pause)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan only")
    parser.add_argument(
        "--keep-open-on-reject",
        action="store_true",
        help="If AI rejects, leave bounty open instead of failing hard",
    )
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:8]
    marker = f"coldstart-{run_id}"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    buyer_name = f"ColdBuyer-{run_id}"
    seller_name = f"ColdSeller-{run_id}"

    print("=" * 60)
    print("Cold-start marketplace demo")
    print(f"  run marker: {marker}")
    print(f"  reward:     {args.reward} ATE")
    print(f"  agents UI:  {MARKET}/agents")
    print(f"  bounties:   {MARKET}/bounties")
    print("=" * 60)

    if args.dry_run:
        print("DRY RUN — would register buyer+seller, publish card, post/claim/settle.")
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Register strangers ──────────────────────────────────────────
    print("\n[1] Register buyer + seller on the exchange")
    buyer = register(
        buyer_name,
        f"cold-buyer-{run_id}",
        f"cold-buyer-{run_id}@example.com",
    )
    time.sleep(1.5)  # registration rate-limit courtesy
    seller = register(
        seller_name,
        f"cold-seller-{run_id}",
        f"cold-seller-{run_id}@example.com",
    )
    print(f"  buyer  {buyer['account_id']}  {buyer_name}")
    print(f"  seller {seller['account_id']}  {seller_name}")
    print(f"  buyer balance:  {balance(buyer['api_key']).get('available')}")
    print(f"  seller balance:  {balance(seller['api_key']).get('available')}")
    print(f"  watch: {MARKET}/agents/{buyer['account_id']}")
    print(f"  watch: {MARKET}/agents/{seller['account_id']}")

    pause(args.pause, f"Find {buyer_name} and {seller_name} on {MARKET}/agents")

    # ── 2. Seller publishes Agent Card ─────────────────────────────────
    print("\n[2] Seller publishes Agent Card")
    publish_card(seller)
    print(f"  card: {EXCHANGE}/v1/accounts/{seller['account_id']}/card")
    print(f"  profile should show 'Agent Card published'")
    pause(args.pause, f"Open seller profile {MARKET}/agents/{seller['account_id']}")

    # ── 3. Buyer posts + funds bounty ──────────────────────────────────
    print("\n[3] Buyer posts + funds open bounty")
    buyer_tok = market_login(buyer["api_key"])
    title = f"Cold-start echo — {marker}"
    bounty_body = {
        "title": title,
        "description": (
            f"Cold-start demo bounty ({marker}). "
            "Any servicer may claim. Deliver application/json with "
            '{"ok": true, "marker": "' + marker + '", "seller_bot": "<your bot_name>"}.'
        ),
        "tags": ["cold-start", "demo", "echo", marker],
        "reward_amount": args.reward,
        "max_claims": 1,
        "difficulty": "trivial",
        "auto_approve": False,
        "provenance_tier": "tier1_self_declared",
        "acceptance_criteria": {
            "description": (
                "Deliverable content_type application/json; body parses with json.loads; "
                f'includes ok=true and marker="{marker}".'
            ),
            "output_format": "application/json",
            "provenance_tier": "tier1_self_declared",
            "required_sources": None,
            "custom_checks": None,
        },
    }
    st, created = http(
        f"{MARKET}/api/bounties",
        method="POST",
        body=bounty_body,
        headers=bearer(buyer_tok),
    )
    if st not in (200, 201) or not isinstance(created, dict) or not created.get("id"):
        raise SystemExit(f"create bounty failed ({st}): {created}")
    bounty_id = created["id"]

    st, funded = http(
        f"{MARKET}/api/bounties/{bounty_id}/fund",
        method="POST",
        headers=bearer(buyer_tok),
    )
    if st != 200:
        raise SystemExit(f"fund failed ({st}): {funded}")

    bounty_url = f"{MARKET}/bounties/{bounty_id}"
    print(f"  bounty_id: {bounty_id}")
    print(f"  status:    open (funded)")
    print(f"  watch:     {bounty_url}")
    print(f"  feed:      {MARKET}/bounties  (Open Bounties tab)")
    pause(args.pause, "Confirm bounty visible under Open Bounties")

    # ── 4. Seller discovers + claims (no buyer account_id) ─────────────
    print("\n[4] Seller discovers open bounty via marketplace feed")
    seller_tok = market_login(seller["api_key"])
    found = seller_find_bounty(seller_tok, marker)
    if found["id"] != bounty_id:
        print(f"  warning: discovered {found['id']} (expected {bounty_id})")
    print(f"  discovered: {found['title']}")

    st, claimed = http(
        f"{MARKET}/api/bounties/{found['id']}/claim",
        method="POST",
        body={},
        headers=bearer(seller_tok),
    )
    if st not in (200, 201) or not isinstance(claimed, dict) or not claimed.get("id"):
        raise SystemExit(f"claim failed ({st}): {claimed}")
    claim_id = claimed["id"]
    print(f"  claim_id: {claim_id}")
    print(f"  watch:    {MARKET}/bounties  (In Progress tab)")
    pause(args.pause, "Confirm bounty moved to In Progress / claimed")

    # ── 5. Seller submits ──────────────────────────────────────────────
    print("\n[5] Seller submits JSON deliverable")
    deliverable = {
        "ok": True,
        "marker": marker,
        "seller_bot": seller_name,
        "seller_account_id": seller["account_id"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    st, submitted = http(
        f"{MARKET}/api/claims/{claim_id}/submit",
        method="POST",
        body={
            "deliverable": {
                "content": json.dumps(deliverable),
                "content_type": "application/json",
                "metadata": {"demo": "cold-start", "marker": marker},
            },
            "provenance": {
                "source_type": "generated",
                "attestation_level": "self_declared",
                "source_refs": [f"{MARKET}/agents/{seller['account_id']}"],
            },
        },
        headers=bearer(seller_tok),
    )
    if st not in (200, 201) or not isinstance(submitted, dict) or not submitted.get("id"):
        raise SystemExit(f"submit failed ({st}): {submitted}")
    submission_id = submitted["id"]
    print(f"  submission_id: {submission_id}")
    pause(args.pause, "Bounty should be in_review under In Progress")

    # ── 6. Buyer reviews + approves ────────────────────────────────────
    print("\n[6] Buyer waits for AI review, then approves")
    review = poll_ai(buyer_tok, submission_id)
    ai = review.get("ai_review") or {}
    score = ai.get("score")
    rec = (ai.get("recommendation") or "").lower()
    print(f"  AI score={score} rec={rec or 'n/a'} status={review.get('status')}")

    if rec == "reject" and not args.keep_open_on_reject:
        st, rejected = http(
            f"{MARKET}/api/submissions/{submission_id}/reject",
            method="POST",
            body={"notes": f"cold-start demo: AI reject score={score}"},
            headers=bearer(buyer_tok),
        )
        print(f"  rejected ({st})")
        print(f"  bounty returns to Open: {bounty_url}")
        _save_state(run_id, marker, buyer, seller, bounty_id, "rejected")
        return 1

    st, approved = http(
        f"{MARKET}/api/submissions/{submission_id}/approve",
        method="POST",
        body={
            "notes": f"cold-start demo approve marker={marker}",
            "score": int(score) if score is not None else 90,
        },
        headers=bearer(buyer_tok),
    )
    if st != 200:
        raise SystemExit(f"approve failed ({st}): {approved}")
    print(f"  approved ({st})")
    print(f"  watch Completed: {MARKET}/bounties")
    print(f"  bounty: {bounty_url}")

    print("\n[7] Balances after settlement")
    print(f"  buyer:  {balance(buyer['api_key'])}")
    print(f"  seller: {balance(seller['api_key'])}")

    _save_state(run_id, marker, buyer, seller, bounty_id, "completed")
    print("\nPASS — cold-start loop finished.")
    print(f"  buyer profile:  {MARKET}/agents/{buyer['account_id']}")
    print(f"  seller profile:  {MARKET}/agents/{seller['account_id']}")
    print(f"  bounty:          {bounty_url}")
    return 0


def _save_state(run_id, marker, buyer, seller, bounty_id, status) -> None:
    path = STATE_DIR / f"{run_id}.json"
    # Never persist raw api_keys in a shared path if avoidable — store ids only.
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "marker": marker,
                "status": status,
                "buyer_account_id": buyer["account_id"],
                "buyer_bot_name": buyer["bot_name"],
                "seller_account_id": seller["account_id"],
                "seller_bot_name": seller["bot_name"],
                "bounty_id": bounty_id,
                "bounty_url": f"{MARKET}/bounties/{bounty_id}",
                "agents_url": f"{MARKET}/agents",
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
    print(f"  state: {path}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        raise SystemExit(130)
