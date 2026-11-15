# Droplet plan 43: agent ownership hotfix

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-14 after the
marketplace agent-management audit. Production exchange never had
`/v1/accounts/managed-bots` or `rotate-key-managed` (404 into account lookup).
Local/prod marketplace BFF had diverged; this plan deploys exchange ownership
routes plus a marketplace BFF that uses the caller's exchange key only (no
dashboard key for suspend / unsuspend / rotate).

**This plan:**

- Deploys `a2a-settlement` (exchange) then `settlebridge-ai` (marketplace BFF).
- Creates `developer_id_owners` (via `create_all`) and adds `accounts.is_test`.
- Grandfathers every existing `developer_id` as `legacy_open` on exchange start.
- First authenticated `GET /v1/accounts/managed` (or
  `POST /v1/accounts/developer-ids/claim`) claims that developer ID for the
  caller's account. Later claimers get 409. New registrations under a claimed
  ID require `owner_api_key`.
- Does **not** ship the My agents UI / `AGENT_MANAGEMENT_V2` flag.
- Does **not** change settlement, escrow, or reputation scoring.
- Does **not** print `.env` values, tokens, or keys.

Pinned commits (fill in after push):

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| a2a-settlement | `/home/clawdbot/a2a-settlement` | `5f4bdab0d8061434388ed9ab2b3e70d19b4ad3e0` |
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `307a100ddb96cc65a27ed996b8170c6112b6f511` |

## Rules (same as plans 1–42)

- Run blocks as `root`, one block at a time: wait for each block to finish and
  check its result before starting the next. Each uses `set -euo pipefail` and
  stops on the first failure. If any command or `test` fails, **stop and
  report** the failing line and its output. Do not work around it.
- Never print `.env` values, tokens or keys. Don't pipe secrets into
  `tee /dev/stderr`.
- `git status` on each repo must be clean (or only expected docs dirt) before
  pull. If unexpected dirty files block ff-only, stop and report.
- Run Python as `clawdbot` when using app venvs.

## P.1 Pre-flight (read-only)

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
SB=/home/clawdbot/settlebridge-ai
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -Atc "$1"; }

systemctl is-active --quiet a2a-exchange
systemctl is-active --quiet settlebridge
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200

# managed-bots must still be missing before deploy (catch-all 404)
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed-bots | grep -qx 404

cd "$EX" && git rev-parse --short HEAD && git status -sb | head -20
cd "$SB" && git rev-parse --short HEAD && git status -sb | head -20

xe "select count(*) from accounts"
xe "select count(distinct developer_id) from accounts"
# table should not exist yet (or already from a prior attempt — report either way)
xe "select to_regclass('public.developer_id_owners')" || true
xe "select count(*) from information_schema.columns where table_name='accounts' and column_name='is_test'"
```

## P.2 Deploy exchange

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
cd "$EX"
git status
git fetch origin
# Plan 40 left staged docs-site/site edits; stash them so ff-only can proceed.
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  sudo -u clawdbot git stash push -u -m "plan43-pre-deploy-dirt" -- docs-site site migrate_to_postgres.py || true
fi
git merge --ff-only 5f4bdab0d8061434388ed9ab2b3e70d19b4ad3e0

sudo -u clawdbot "$EX/.venv/bin/python" -m pytest tests/test_managed_ownership.py -q

systemctl restart a2a-exchange
for i in $(seq 1 30); do systemctl is-active --quiet a2a-exchange && break; sleep 1; done
systemctl is-active a2a-exchange
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
```

## P.3 Verify exchange schema + managed route shape

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -Atc "$1"; }

test "$(xe "select to_regclass('public.developer_id_owners')")" = "developer_id_owners"
test "$(xe "select count(*) from information_schema.columns where table_name='accounts' and column_name='is_test'")" = 1
# grandfather: every distinct developer_id has a row
test "$(xe "select count(*) from developer_id_owners")" -ge "$(xe "select count(distinct developer_id) from accounts")"

# unauthenticated managed must 401
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401
```

## P.4 Deploy settlebridge BFF

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
cd "$SB"
git status
git fetch origin
git merge --ff-only 307a100ddb96cc65a27ed996b8170c6112b6f511

# optional: run ownership unit tests in backend venv if present
if [[ -x "$SB/backend/.venv/bin/python" ]]; then
  sudo -u clawdbot "$SB/backend/.venv/bin/python" -m pytest tests/test_bots_ownership.py -q
elif [[ -x "$SB/.venv/bin/python" ]]; then
  sudo -u clawdbot bash -lc "cd $SB/backend && $SB/.venv/bin/python -m pytest tests/test_bots_ownership.py -q"
fi

systemctl restart settlebridge
for i in $(seq 1 30); do systemctl is-active --quiet settlebridge && break; sleep 1; done
systemctl is-active settlebridge
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200
```

## P.5 Smoke (authenticated, no key printed)

Uses the operator's linked account from the settlebridge DB. The helper reads
the key in-process and never prints it.

```bash
set -euo pipefail
install -o clawdbot -g clawdbot -m 600 /dev/null /home/clawdbot/ownership-smoke.py
cat > /home/clawdbot/ownership-smoke.py <<'PY'
"""Ownership hotfix smoke. Never prints keys."""
import asyncio, os, sys
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

EMAIL = os.environ.get("SMOKE_EMAIL", "rich@truthsetter.com")
EX = "http://127.0.0.1:8000/v1"
SB = "http://127.0.0.1:8002/api"


async def key_for(email: str) -> tuple[str, str]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        row = (
            await conn.execute(
                text(
                    "select exchange_bot_id, exchange_api_key from users "
                    "where email=:email and exchange_api_key is not null"
                ),
                {"email": email},
            )
        ).first()
    await engine.dispose()
    if row is None:
        raise SystemExit(f"no linked account for {email}")
    return row[0], row[1]


async def main() -> None:
    bot_id, api_key = await key_for(EMAIL)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{EX}/accounts/managed", headers=headers)
        r.raise_for_status()
        body = r.json()
        assert "bots" in body and "developer_id" in body
        assert all("api_key" not in b for b in body["bots"])
        # cross-owner: random UUID must 403
        bad = await client.post(
            f"{EX}/accounts/00000000-0000-0000-0000-000000000001/suspend",
            headers=headers,
        )
        assert bad.status_code in (403, 404), bad.status_code
        # marketplace list (needs a JWT — skip if we only have ate_ key)
        print(
            f"OK managed count={body.get('count')} "
            f"developer_id_set={bool(body.get('developer_id'))} "
            f"own={bot_id in {b['id'] for b in body['bots']}}"
        )


asyncio.run(main())
PY
chown clawdbot:clawdbot /home/clawdbot/ownership-smoke.py
chmod 600 /home/clawdbot/ownership-smoke.py

# DATABASE_URL from settlebridge .env without printing it
sudo -u clawdbot bash -lc '
set -euo pipefail
set -a
source /home/clawdbot/settlebridge-ai/.env
set +a
cd /home/clawdbot/settlebridge-ai/backend
# prefer app venv if present
PY=python3
[[ -x /home/clawdbot/settlebridge-ai/backend/.venv/bin/python ]] && PY=/home/clawdbot/settlebridge-ai/backend/.venv/bin/python
[[ -x /home/clawdbot/settlebridge-ai/.venv/bin/python ]] && PY=/home/clawdbot/settlebridge-ai/.venv/bin/python
$PY /home/clawdbot/ownership-smoke.py
'
```

## P.6 Rollback

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
SB=/home/clawdbot/settlebridge-ai
# replace with pre-deploy SHAs recorded in P.1
cd "$EX" && git merge --ff-only 7a65d4896a832374641284322ff2234a72dedb09
cd "$SB" && git merge --ff-only fa8c148241388383250b264b4944e1a53a112fa7
systemctl restart a2a-exchange settlebridge
for u in a2a-exchange settlebridge; do
  for i in $(seq 1 30); do systemctl is-active --quiet $u && break; sleep 1; done
  systemctl is-active $u
done
```

Do **not** drop `developer_id_owners` or `accounts.is_test` on rollback unless
explicitly asked — additive schema stays.

## Done when

- P.3 shows `developer_id_owners` populated and `is_test` present
- P.5 prints `OK managed count=…`
- Cross-owner suspend is 403/404
- `journalctl -u a2a-exchange -u settlebridge -n 50 --no-pager` shows no traceback storm
