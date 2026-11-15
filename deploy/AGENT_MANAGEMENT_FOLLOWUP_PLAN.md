# Droplet plan 47: claimed-ID attach follow-up

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-15.

**Depends on plan 46 (already finished; rollback was not run):**

- Exchange at `3ce5e634a3e396990b9c686044cb3ce51195265a`
- SettleBridge at `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1`
- `AGENT_MANAGEMENT_V2` is already true. Do not edit `.env`.

**This plan:**

- Fast-forwards `a2a-settlement` to `2a2e2bca9171cb8a1914887a99d13298b24a0a22`
  (attach of a Developer ID claimed by another operator returns 403).
  Registration under a claimed ID was already 403. No schema change.
  No grandfather migration.
- Fast-forwards `settlebridge-ai` to `f916cd5c5b4782b971b7e0ff82d197b214811ed9`
  (My agents dialog copy, dashboard suspended counts exclude `is_test` via
  `visibleManagedAgents`, loading skeletons instead of a false 0). That
  commit's history includes docs commit `46d9319b15de0bd280d3a6328d8ae83647069d1f`
  (plan 46). The deploy pin is `f916cd5c5b4782b971b7e0ff82d197b214811ed9`.
  The docs commit that adds this plan file is not a deploy pin. Do not
  fast-forward to it.
- Restarts `a2a-exchange` and waits for `http://127.0.0.1:8000/health` 200.
- Restarts `settlebridge` and waits for `http://127.0.0.1:8002/health` 200.
- Rebuilds and restarts `settlebridge-marketplace` the same way as plans
  45 and 46 (memory check `free -m` available >= 1150, build as `clawdbot`,
  copy standalone static and public).
- Smoke-checks local health 200, `https://market.settlebridge.ai/agents`
  and `/dashboard` 200, `/api/config` still `agent_management_v2` true,
  unauthenticated `POST /v1/accounts/developer-ids/attach` 401 or 403, and
  the built bundle contains `Loading suspended count`.

**This plan does not:**

- Migrate `legacy_open` rows.
- Publish Agent Cards.
- Change `is_test`, `developer_id_owners`, accounts, or cards.
- Change `AGENT_MANAGEMENT_V2` or print `.env` values, tokens, or keys.
- Run SQL. There is no database block.

Pinned commits (deploy these, not a later docs-only tip):

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| a2a-settlement | `/home/clawdbot/a2a-settlement` | `2a2e2bca9171cb8a1914887a99d13298b24a0a22` |
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `f916cd5c5b4782b971b7e0ff82d197b214811ed9` |

The SettleBridge deploy pin is `f916cd5c5b4782b971b7e0ff82d197b214811ed9`, not
the docs commit that adds this file.

Expected heads before this plan (P.1 records the actual values and refuses
to continue on any other head):

| Repo | Required current HEAD |
|------|------------------------|
| a2a-settlement | `3ce5e634a3e396990b9c686044cb3ce51195265a` |
| settlebridge-ai | `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1` |

## Rules (same as plans 1–46)

- Run blocks as `root`, one block at a time: wait for each block to finish and
  check its result before starting the next. Each uses `set -euo pipefail`
  (D.4 collects failures, then exits non-zero if any check failed) and stops
  on the first failure. If any command or `test` fails, **stop and report**
  the failing line and its output. Do not work around it.
- Never print `.env` values, tokens, or keys. Don't pipe secrets into
  `tee /dev/stderr`. The attach smoke discards the response body.
- `git status` on each repo must be clean (or only expected dirt) before
  pull. Stash only when dirt would block ff-only. Exchange: stash only
  `docs-site`, `site`, and `migrate_to_postgres.py`. SettleBridge:
  `git stash push -u`. If other exchange files are still dirty, stop and
  report. Do not stash unrelated exchange paths.
- Run git / npm as `clawdbot`.
- One SSH session. Do not open a burst of new SSH connections.
- P.1 is read-only. It must succeed before D.1. If it exits, stop. Do not
  run D.1, D.2, D.3, D.4, or P.2.
- P.2 is rollback only. Do not run it unless a later block failed or you are
  reverting this plan.

## P.1 Pre-flight (read-only)

No SQL. Does not read or write `is_test`, `developer_id_owners`, accounts,
or cards. Refuses to continue unless both heads match plan 46.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PLAN=/root/deploy-plans/2026-11-15-agent-management-followup/AGENT_MANAGEMENT_FOLLOWUP_PLAN.md

systemctl is-active --quiet a2a-exchange
systemctl is-active --quiet settlebridge
systemctl is-active --quiet settlebridge-marketplace
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:3001/agents | grep -qx 200

EX_HEAD=$(git -C "$EX" rev-parse HEAD)
SB_HEAD=$(git -C "$SB" rev-parse HEAD)
echo "EX_HEAD=$EX_HEAD"
echo "SB_HEAD=$SB_HEAD"
if [ "$EX_HEAD" != "3ce5e634a3e396990b9c686044cb3ce51195265a" ]; then
  echo "unexpected exchange HEAD; refusing to continue"
  exit 1
fi
if [ "$SB_HEAD" != "2551ea4abce87c6b49695566b7b2ea6e88e7cbe1" ]; then
  echo "unexpected settlebridge HEAD; refusing to continue"
  exit 1
fi
printf '%s\n' "$EX_HEAD" > /root/plan47-prev-exchange.sha
printf '%s\n' "$SB_HEAD" > /root/plan47-prev-settlebridge.sha

curl -s -m 10 http://127.0.0.1:8002/api/config | grep -Eq '"agent_management_v2":[[:space:]]*true'
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401

cd "$SB"
git status -sb
test -f marketplace-next/.next/standalone/server.js

if grep -qE '<[A-Z]+_(PREV_)?SHA>' "$PLAN"; then
  echo "PLAN STILL HAS PLACEHOLDER SHAS — stop"
  exit 1
fi
echo "P.1 OK"
```

Record `EX_HEAD` and `SB_HEAD`. Continue only when both match and the block
prints `P.1 OK`.

## D.1 Deploy exchange

Run only after P.1 prints `P.1 OK`. No migration. No SQL.

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
cd "$EX"
git fetch origin
known=0
if ! git diff --quiet -- docs-site site migrate_to_postgres.py \
  || ! git diff --cached --quiet -- docs-site site migrate_to_postgres.py \
  || [ -n "$(git ls-files --others --exclude-standard -- docs-site site migrate_to_postgres.py | head -1)" ]; then
  known=1
fi
if [ "$known" -eq 1 ]; then
  echo "dirty exchange tree; stashing docs-site site migrate_to_postgres.py"
  git status -sb
  sudo -u clawdbot git stash push -u -m "plan47-ex-pre-deploy-dirt" -- docs-site site migrate_to_postgres.py
fi
if ! git diff --quiet || ! git diff --cached --quiet \
  || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  echo "unexpected exchange dirt still blocks ff-only"
  git status -sb
  exit 1
fi
sudo -u clawdbot git merge --ff-only 2a2e2bca9171cb8a1914887a99d13298b24a0a22
test "$(git rev-parse HEAD)" = "2a2e2bca9171cb8a1914887a99d13298b24a0a22"
echo "EX_NOW=$(git rev-parse HEAD) $(git log -1 --oneline)"
systemctl restart a2a-exchange
for i in $(seq 1 30); do systemctl is-active --quiet a2a-exchange && break; sleep 1; done
systemctl is-active a2a-exchange
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8000/health || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401
```

## D.2 Deploy SettleBridge code

Fast-forward to the feature commit. The deploy pin is
`f916cd5c5b4782b971b7e0ff82d197b214811ed9`, not the docs commit that adds
this plan file. Do not `git merge` the tip of `origin/main`.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
cd "$SB"
git status -sb
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet \
  || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  echo "dirty settlebridge tree; stashing before ff-only"
  git status -sb
  sudo -u clawdbot git stash push -u -m "plan47-pre-deploy-dirt"
fi
sudo -u clawdbot git merge --ff-only f916cd5c5b4782b971b7e0ff82d197b214811ed9
test "$(git rev-parse HEAD)" = "f916cd5c5b4782b971b7e0ff82d197b214811ed9"
echo "SB_NOW=$(git rev-parse HEAD) $(git log -1 --oneline)"
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

## D.3 Build and restart marketplace

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
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:3001/agents || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
echo "MARKETPLACE OK"
```

If the memory check fails: start `settlebridge-marketplace` again, **stop and
report**. Do not force a build under memory pressure. Do not run D.4.

## D.4 Smoke (no keys printed)

`Loading suspended count` is the My agents aria-label added in
`f916cd5c5b4782b971b7e0ff82d197b214811ed9`. It is absent from
`2551ea4abce87c6b49695566b7b2ea6e88e7cbe1`. Do not substitute `Show suspended`;
that string is already in the live bundle. Discard the attach response body.
Do not print the config body.

```bash
set -uo pipefail
MK=https://market.settlebridge.ai
M=/home/clawdbot/settlebridge-ai/marketplace-next
FAIL=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }

for u in \
  http://127.0.0.1:8000/health \
  http://127.0.0.1:8002/health \
  http://127.0.0.1:3001/agents \
  "$MK/agents" \
  "$MK/dashboard"
do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u" || true)
  [ "$code" = 200 ] && ok "$code $u" || bad "$code $u"
done

curl -s -m 10 "$MK/api/config" | grep -Eq '"agent_management_v2":[[:space:]]*true' \
  && ok "api/config agent_management_v2=true" \
  || bad "api/config missing agent_management_v2=true"

code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  http://127.0.0.1:8000/v1/accounts/developer-ids/attach \
  -H 'content-type: application/json' \
  -d '{"agent_api_key":"ate_not_a_real_key"}' || true)
[ "$code" = 401 ] || [ "$code" = 403 ] && ok "$code attach unauth" || bad "$code attach unauth"

grep -rq "Loading suspended count" "$M/.next/standalone/.next/static/chunks" \
  && ok "bundle includes Loading suspended count" \
  || bad "bundle missing Loading suspended count"

if [ "$FAIL" -ne 0 ]; then
  echo "PLAN 47 SMOKE FAILED"
  exit 1
fi
echo "PLAN 47 SMOKE OK"
```

## P.2 Rollback

Run only if a later block failed or you are reverting this plan. Resets both
repos to the SHAs saved in P.1 and rebuilds the marketplace.

This block does not change `is_test`, `developer_id_owners`, accounts, or
cards. It does not migrate `legacy_open` rows and does not publish Agent
Cards. No SQL. Do not print `.env`.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
echo "rollback resets git SHAs and rebuilds marketplace; no database writes"

test -f /root/plan47-prev-exchange.sha
test -f /root/plan47-prev-settlebridge.sha
EX_PREV=$(tr -d '[:space:]' < /root/plan47-prev-exchange.sha)
SB_PREV=$(tr -d '[:space:]' < /root/plan47-prev-settlebridge.sha)
test "$EX_PREV" = "3ce5e634a3e396990b9c686044cb3ce51195265a"
test "$SB_PREV" = "2551ea4abce87c6b49695566b7b2ea6e88e7cbe1"

cd "$EX"
sudo -u clawdbot git reset --hard "$EX_PREV"
test "$(git rev-parse HEAD)" = "$EX_PREV"
cd "$SB"
sudo -u clawdbot git reset --hard "$SB_PREV"
test "$(git rev-parse HEAD)" = "$SB_PREV"
systemctl restart a2a-exchange settlebridge

M=$SB/marketplace-next
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
systemctl restart settlebridge-marketplace
for u in a2a-exchange settlebridge settlebridge-marketplace; do
  for i in $(seq 1 30); do systemctl is-active --quiet $u && break; sleep 1; done
  systemctl is-active $u
done
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8000/health || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8002/health || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
code=000
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:3001/agents || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
echo "ROLLBACK OK"
```

If the memory check fails: start `settlebridge-marketplace` again, **stop and
report**. Do not force a build under memory pressure. The git reset has
already run; report both heads before retrying only the marketplace rebuild.

## Done when

- Exchange HEAD is `2a2e2bca9171cb8a1914887a99d13298b24a0a22`
- SettleBridge HEAD is `f916cd5c5b4782b971b7e0ff82d197b214811ed9`
  (not the docs commit that adds this file)
- D.4 prints `PLAN 47 SMOKE OK`
- `AGENT_MANAGEMENT_V2` is still true (do not print the env file)
- No `legacy_open` migration ran, and no Agent Card was published
- `is_test`, `developer_id_owners`, accounts, and cards were not written
