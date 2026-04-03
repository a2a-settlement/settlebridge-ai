"""
End-to-end integration test for the Self-Improving Agent Training Loop.

Prerequisites (already running):
  - SettleBridge backend: http://localhost:8080
  - Mock mediator:        http://localhost:9000
  - A2A Exchange:         http://localhost:8000

Run:
    python3 test_training_loop.py

Cleanup (removes ALL test data created by this script):
    python3 test_training_loop.py --teardown
"""

import sys
import json
import uuid
import logging
import argparse
import httpx

logging.basicConfig(level=logging.ERROR, format="%(name)s %(levelname)s: %(message)s")

SB = "http://localhost:8080"
EX = "http://localhost:8000"

TEST_EMAIL    = "training-test@example.com"
TEST_PASSWORD = "training-test-pw-1234"
TEST_BOT_NAME = f"sb-training-test-bot-{uuid.uuid4().hex[:8]}"

STATE_FILE = "/tmp/sb_training_test_state.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def sb(method, path, token=None, **kwargs):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.request(method, f"{SB}{path}", headers=headers, timeout=30, **kwargs)
    if not r.is_success:
        print(f"  ERROR {method} {path} → {r.status_code}: {r.text[:400]}")
    r.raise_for_status()
    return r.json()


def ex(method, path, api_key=None, **kwargs):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = httpx.request(method, f"{EX}{path}", headers=headers, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"\n[state saved to {STATE_FILE}]")


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


# ── teardown ─────────────────────────────────────────────────────────────────

def teardown():
    import subprocess, os

    try:
        state = load_state()
    except FileNotFoundError:
        print("No state file found — nothing to tear down.")
        return

    user_id      = state.get("user_id")
    exchange_api = state.get("exchange_api_key")
    exchange_bot = state.get("exchange_bot_id")
    token        = state.get("token")

    print("\n=== TEARDOWN ===")

    # 1. Delete training data via raw SQL (cascades handle children)
    import subprocess
    psql = ["psql", "-h", "localhost", "-U", "settlebridge", "-d", "settlebridge", "-c"]
    env  = {**os.environ, "PGPASSWORD": "settlebridge"}

    if user_id:
        # Delete in FK-safe order
        for sql in [
            f"DELETE FROM training_transcripts WHERE training_run_id IN (SELECT id FROM training_runs WHERE agent_user_id = '{user_id}');",
            f"DELETE FROM score_history WHERE agent_user_id = '{user_id}';",
            f"DELETE FROM submissions WHERE agent_user_id = '{user_id}';",
            f"DELETE FROM claims WHERE agent_user_id = '{user_id}';",
            f"DELETE FROM training_runs WHERE agent_user_id = '{user_id}';",
            f"DELETE FROM notifications WHERE user_id = '{user_id}';",
            f"DELETE FROM bounties WHERE requester_id = '{user_id}';",
            f"DELETE FROM users WHERE id = '{user_id}';",
        ]:
            result = subprocess.run(psql + [sql], env=env, capture_output=True, text=True)
            msg = result.stdout.strip() or result.stderr.strip()
            print(f"  SQL: {sql[:60]}...  → {msg}")

    # 2. Deposit any remaining balance back (or just leave it — test bots are cheap)
    if exchange_api and exchange_bot:
        try:
            bal = ex("GET", "/v1/exchange/balance", api_key=exchange_api)
            available = bal.get("available", 0)
            print(f"  Exchange bot remaining balance: {available} ATE (left in place)")
        except Exception as e:
            print(f"  Could not check exchange balance: {e}")

    # 3. Remove state file
    try:
        os.remove(STATE_FILE)
        print(f"  Removed {STATE_FILE}")
    except FileNotFoundError:
        pass

    print("\nTeardown complete.")


# ── main test ─────────────────────────────────────────────────────────────────

def run_test():
    state = {}

    print("=" * 60)
    print("Self-Improving Agent Training Loop — Integration Test")
    print("=" * 60)

    # ── Step 1: Register SettleBridge user ─────────────────────────
    print("\n[1] Registering test user ...")
    try:
        data = sb("POST", "/api/auth/register", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "display_name": "Training Test Agent",
            "user_type": "both",
        })
        token = data["access_token"]
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            print("  User already exists — logging in")
            data = sb("POST", "/api/auth/login", json={
                "email": TEST_EMAIL, "password": TEST_PASSWORD
            })
            token = data["access_token"]
        else:
            raise

    me = sb("GET", "/api/auth/me", token=token)
    user_id = me["id"]
    state["token"] = token
    state["user_id"] = user_id
    print(f"  OK  user_id={user_id}")

    # ── Step 2: Link exchange account ──────────────────────────────
    print("\n[2] Linking exchange bot ...")
    if me.get("exchange_bot_id"):
        print(f"  Already linked: {me['exchange_bot_id']}")
        state["exchange_bot_id"] = me["exchange_bot_id"]
        state["exchange_api_key"] = me.get("exchange_api_key") or ""
    else:
        linked = sb("POST", "/api/auth/link-exchange", token=token, json={
            "bot_name": TEST_BOT_NAME,
            "developer_id": "test-developer",
        })
        state["exchange_bot_id"] = linked["exchange_bot_id"]
        state["exchange_api_key"] = linked.get("exchange_api_key") or ""
        print(f"  OK  bot_id={state['exchange_bot_id']}")

    save_state(state)

    # ── Step 3: Deposit ATE so the bot can fund micro-stake escrows ─
    print("\n[3] Depositing 500 ATE to test bot ...")
    try:
        dep = ex("POST", "/v1/exchange/deposit", api_key=state["exchange_api_key"],
                 json={"amount": 500, "currency": "ATE", "reference": "training-test-deposit"})
        print(f"  OK  balance after deposit: {dep.get('balance', {}).get('available', '?')}")
    except Exception as e:
        print(f"  Deposit failed (may already have balance): {e}")

    # ── Step 4: Create training bounty ─────────────────────────────
    print("\n[4] Creating training bounty ...")
    bounty = sb("POST", "/api/bounties", token=token, json={
        "title": "Summarize financial filing (training)",
        "description": "Summarize a 10-K SEC filing for a technology company.",
        "acceptance_criteria": {
            "description": "Summary must cover revenue, net income, and FY guidance. Max 300 words.",
            "output_format": "text",
        },
        "reward_amount": 50,
        "mode": "training",
        "difficulty": "medium",
        "max_claims": 20,   # allow many training iterations to claim
    })
    bounty_id = bounty["id"]
    state["bounty_id"] = bounty_id
    save_state(state)
    print(f"  OK  bounty_id={bounty_id}  status={bounty['status']}")

    # ── Step 5: Fund/open the bounty ──────────────────────────────
    print("\n[5] Funding bounty (marks it OPEN) ...")
    opened = sb("POST", f"/api/bounties/{bounty_id}/fund", token=token)
    print(f"  OK  status={opened.get('status')}")

    # ── Step 6: Run the harness ────────────────────────────────────
    print("\n[6] Running training harness (max 6 iterations, threshold 0.85) ...")

    sys.path.insert(0, "/root/settlebridge-ai/harness")
    from harness import TrainingHarness

    def mutation_callback(reasoning: str, diagnostics: dict) -> dict:
        gaps = diagnostics.get("actionable_gaps", [])
        print(f"    ↳ gaps={gaps}")
        parts = [
            "Q3 revenue: $4.2B (+12% YoY).",
            "Net income: $1.1B (26% margin).",
            "FY guidance: management projects 8-10% revenue growth.",
        ]
        # progressively include more info as gaps close
        if "Revenue figure missing" not in gaps:
            pass  # already included
        if not gaps:
            parts.append("Free cash flow: $800M. EPS: $2.34.")
        return {"content": " ".join(parts) + f" (revision targeting: {'; '.join(gaps) or 'all criteria met'})",
                "format": "text"}

    harness = TrainingHarness(
        api_url=SB,
        api_key=token,
        target_bounty_id=bounty_id,
        max_iterations=6,
        stake_budget=600,
        score_threshold=0.85,
        mutation_callback=mutation_callback,
        initial_deliverable={
            "content": "The company reported strong results this quarter.",
            "format": "text",
        },
        task_type="summarization",
    )

    transcript = harness.run()
    state["transcript"] = transcript
    save_state(state)

    # ── Step 7: Print results ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Total iterations:   {transcript['total_iterations']}")
    print(f"  Total stake spent:  {transcript['total_stake_spent']} ATE")
    print(f"  Final training EMA: {transcript['final_training_ema']:.4f}")
    print(f"  Merkle root:        {transcript['merkle_root'][:20]}...")
    print(f"  Transcript ID:      {transcript['id']}")
    print()

    payload = transcript["signed_payload"]
    print("  Score trajectory:", payload["score_trajectory"])
    print()
    print("  Attempts:")
    for a in payload["attempts"]:
        print(f"    iter {a['iteration']}: score={a['numeric_score']:.4f}  "
              f"gaps={a['diagnostics'].get('actionable_gaps', [])}")

    print("\n✓ Test passed. Run with --teardown to remove all test data.")
    print(f"  State file: {STATE_FILE}")


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--teardown", action="store_true",
                        help="Remove all data created by this test")
    args = parser.parse_args()

    if args.teardown:
        teardown()
    else:
        run_test()
