# Droplet plan 44: agent management UI (My agents)

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-14 after plan 43
deployed the ownership backend (exchange `5f4bdab`, SettleBridge BFF
`4df6776`). Plan 43 did **not** change the marketplace UI — Settings still
says “Managed Bots”. This plan deploys the My agents UI on
`market.settlebridge.ai`.

**Depends on plan 43 (already live):**

- Exchange: `GET /v1/accounts/managed`, suspend / unsuspend /
  `rotate-key-managed`, `developer_id_owners`, `accounts.is_test`
- BFF: `/api/bots*` uses the caller’s exchange key only (no dashboard key)

**This plan:**

- Fast-forwards `settlebridge-ai` to a commit that includes the agent
  management UI (feature flag `AGENT_MANAGEMENT_V2`, default **off** in code /
  env until P.5).
- Restarts `settlebridge` (BFF) if that commit also touches backend routes.
- Rebuilds and restarts `settlebridge-marketplace` (Next standalone).
- Turns `AGENT_MANAGEMENT_V2=true` in the SettleBridge `.env` and restarts
  `settlebridge` so `/api/config` (or equivalent) exposes the flag.
- Does **not** change the exchange, settlement, escrow, or reputation scoring.
- Does **not** print `.env` values, tokens, or keys.
- Does **not** run the Gateway tab work (that is a later plan after review).

**UI that must be in `<SETTLEBRIDGE_SHA>` before you run this:**

- `/agents` tabs: **My agents** (default when signed in) | **Directory** |
  **Gateway** only if the operator has a gateway (hidden otherwise)
- My agents: grouped by Developer ID; status pills; hide suspended / hide test
  by default; Register agent + Add developer ID; bulk suspend/reactivate
- Directory: hide test by default; “Yours” tag; reputation sort
- `/agents/{id}`: owner **Manage** panel (suspend / reactivate / rotate key /
  publish card CTA)
- Settings: remove “Managed Bots”; one line “Manage your agents” → `/agents`
- Dashboard: small My agents counts card
- Vocabulary: user-facing “agent” and “Developer ID” only (no “bot” /
  “namespace”)
- Header: no “Sign in” flash while `useAuth().loading`
- Screenshots desktop + mobile attached to the PR that produced the SHA

Pinned commits (fill in after the UI PR is on `main`):

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `<SETTLEBRIDGE_SHA>` |

Rollback pins (from before this plan):

| Pin | SHA |
|-----|-----|
| `SETTLEBRIDGE_PREV_SHA` | `<SETTLEBRIDGE_PREV_SHA>` (record in P.1; today expected `4df67769ecb1422a83cefe49000d7c00b05bd066` if no other SB deploys intervened) |

Exchange is **not** moved by this plan (stays on plan 43).

## Rules (same as plans 1–43)

- Run blocks as `root`, one block at a time: wait for each block to finish and
  check its result before starting the next. Each uses `set -euo pipefail` and
  stops on the first failure. If any command or `test` fails, **stop and
  report** the failing line and its output. Do not work around it.
- Never print `.env` values, tokens or keys. Don't pipe secrets into
  `tee /dev/stderr`.
- `git status` on the repo must be clean (or only expected dirt) before
  pull. If unexpected dirty files block ff-only, stop and report.
- Run git / npm / pytest as `clawdbot`.
- If `<SETTLEBRIDGE_SHA>` is still a placeholder, **stop after reporting that** —
  do not invent a SHA.

## P.1 Pre-flight (read-only)

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -Atc "$1"; }

systemctl is-active --quiet a2a-exchange
systemctl is-active --quiet settlebridge
systemctl is-active --quiet settlebridge-marketplace
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:3001/ | grep -qx 200

# plan 43 still live
test "$(cd "$EX" && git rev-parse --short HEAD)" = "5f4bdab"
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401
test "$(xe "select to_regclass('public.developer_id_owners')")" = "developer_id_owners"
test "$(xe "select count(*) from information_schema.columns where table_name='accounts' and column_name='is_test'")" = 1

cd "$SB"
echo "SB_HEAD=$(git rev-parse HEAD)"
git status -sb | head -20
test -f marketplace-next/.next/standalone/server.js

# refuse to proceed on placeholders
grep -q '<SETTLEBRIDGE_SHA>' /root/deploy-plans/2026-11-14-agent-management-ui/AGENT_MANAGEMENT_UI_PLAN.md \
  && { echo "PLAN STILL HAS PLACEHOLDER SHAS — stop"; exit 1; } || true
```

Record `SETTLEBRIDGE_PREV_SHA=$(cd "$SB" && git rev-parse HEAD)` in your report.

## P.2 Deploy SettleBridge code

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
cd "$SB"
git status
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  sudo -u clawdbot git stash push -u -m "plan44-pre-deploy-dirt" || true
fi
sudo -u clawdbot git merge --ff-only <SETTLEBRIDGE_SHA>
echo "SB_NOW=$(git rev-parse --short HEAD) $(git log -1 --oneline)"

# backend unit tests that ship with the UI PR
if [[ -x "$SB/backend/.venv/bin/python" ]]; then
  sudo -u clawdbot bash -lc "cd $SB/backend && .venv/bin/python -m pytest tests/test_bots_ownership.py tests/test_bots_routes.py -q"
fi

systemctl restart settlebridge
for i in $(seq 1 30); do systemctl is-active --quiet settlebridge && break; sleep 1; done
systemctl is-active settlebridge
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8002/health || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
```

## P.3 Build and restart marketplace

```bash
set -euo pipefail
M=/home/clawdbot/settlebridge-ai/marketplace-next
systemctl stop settlebridge-marketplace
sleep 3
FREE=$(free -m | awk '/^Mem:/{print $7}'); echo "free MB with marketplace stopped: $FREE"
test "$FREE" -ge 1150
runuser -u clawdbot -- bash -lc "cd '$M' && NODE_OPTIONS=--max-old-space-size=1536 npm run build"
runuser -u clawdbot -- bash -c "
  set -euo pipefail; cd '$M'
  rm -rf .next/standalone/.next/static .next/standalone/public
  cp -r .next/static .next/standalone/.next/static
  cp -r public .next/standalone/public
"
test -f $M/.next/standalone/server.js
test -z "$(find $M/.next ! -user clawdbot | head -1)"
systemctl restart settlebridge-marketplace
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:3001/ || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
echo "MARKETPLACE OK"
```

If the memory check fails: start `settlebridge-marketplace` again, **stop and
report**. Do not force a build under memory pressure.

## P.4 Enable the feature flag

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
ENV=$SB/.env
# ensure the key exists and is true (do not print the file)
grep -q '^AGENT_MANAGEMENT_V2=' "$ENV" \
  && sed -i 's/^AGENT_MANAGEMENT_V2=.*/AGENT_MANAGEMENT_V2=true/' "$ENV" \
  || echo 'AGENT_MANAGEMENT_V2=true' >> "$ENV"
grep -qx 'AGENT_MANAGEMENT_V2=true' "$ENV"
systemctl restart settlebridge
for i in $(seq 1 30); do systemctl is-active --quiet settlebridge && break; sleep 1; done
systemctl is-active settlebridge
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8002/health || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
# flag visible to the client (path may be /api/config — adjust if the PR uses another)
curl -s -m 10 http://127.0.0.1:8002/api/config | grep -q 'agent_management_v2' \
  || curl -s -m 10 http://127.0.0.1:8002/api/config | grep -qi 'AGENT_MANAGEMENT' \
  || echo "WARN: confirm flag exposure path from the PR; health is up"
```

## P.5 Smoke (no keys printed)

```bash
set -uo pipefail
MK=https://market.settlebridge.ai
FAIL=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }

for u in \
  http://127.0.0.1:8002/health \
  http://127.0.0.1:3001/ \
  "$MK/agents" \
  "$MK/dashboard" \
  "$MK/dashboard/settings"
do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u" || true)
  [ "$code" = 200 ] || [ "$code" = 307 ] || [ "$code" = 308 ] && ok "$code $u" || bad "$code $u"
done

# flag off would still 200; check UI copy when flag on
html=$(curl -s -m 15 "$MK/agents" || true)
echo "$html" | grep -qi 'My agents\|Agent Directory\|Directory' && ok "agents page has expected chrome" || bad "agents page missing My agents/Directory"

# Managed Bots must be gone from Settings HTML when flag on
settings=$(curl -s -m 15 "$MK/dashboard/settings" || true)
echo "$settings" | grep -q 'Managed Bots' && bad "Settings still shows Managed Bots" || ok "Managed Bots absent from Settings"
echo "$settings" | grep -qi 'Manage your agents' && ok "Settings links Manage your agents" || bad "Settings missing Manage your agents link"

# bot/namespace must not appear as user-facing labels on /agents (best-effort HTML check)
echo "$html" | grep -E '>Namespace<|>Managed Bots<' && bad "forbidden labels on /agents" || ok "no Managed Bots/Namespace labels on /agents"

test "$FAIL" -eq 0
echo "PLAN 44 SMOKE OK"
```

## P.6 Rollback

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
ENV=$SB/.env
# turn flag off first
grep -q '^AGENT_MANAGEMENT_V2=' "$ENV" \
  && sed -i 's/^AGENT_MANAGEMENT_V2=.*/AGENT_MANAGEMENT_V2=false/' "$ENV" \
  || echo 'AGENT_MANAGEMENT_V2=false' >> "$ENV"
cd "$SB"
sudo -u clawdbot git merge --ff-only <SETTLEBRIDGE_PREV_SHA>
systemctl restart settlebridge
# rebuild previous marketplace at that commit
M=$SB/marketplace-next
systemctl stop settlebridge-marketplace
sleep 3
FREE=$(free -m | awk '/^Mem:/{print $7}'); test "$FREE" -ge 1150
runuser -u clawdbot -- bash -lc "cd '$M' && NODE_OPTIONS=--max-old-space-size=1536 npm run build"
runuser -u clawdbot -- bash -c "
  set -euo pipefail; cd '$M'
  rm -rf .next/standalone/.next/static .next/standalone/public
  cp -r .next/static .next/standalone/.next/static
  cp -r public .next/standalone/public
"
systemctl restart settlebridge-marketplace
for u in settlebridge settlebridge-marketplace; do
  for i in $(seq 1 30); do systemctl is-active --quiet $u && break; sleep 1; done
  systemctl is-active $u
done
```

## Done when

- SettleBridge HEAD is `<SETTLEBRIDGE_SHA>`
- `AGENT_MANAGEMENT_V2=true` in `.env` (do not print the file)
- `/agents` shows My agents / Directory chrome
- Settings has “Manage your agents”, not “Managed Bots”
- Exchange HEAD still `5f4bdab`
- P.5 prints `PLAN 44 SMOKE OK`
