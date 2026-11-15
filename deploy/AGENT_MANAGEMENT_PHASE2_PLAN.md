# Droplet plan 45: agent management phase 2

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-15 after plan 44
left the My agents UI live with `AGENT_MANAGEMENT_V2=true`.

**Depends on plan 44 (already live):**

- Exchange ownership, `developer_id_owners`, `accounts.is_test`
- Marketplace flag on, My agents / Directory tabs

**This plan:**

- Fast-forwards `a2a-settlement` to `6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a`
  (multi-Developer-ID attach and detach, expanded `GET /v1/accounts/managed`,
  controller-scoped Agent Card publish, recent settlements). If the droplet
  is still on `3be803f`, this fast-forward also includes `f681ba5` (operator
  suspend route no longer shadowed).
- Fast-forwards `settlebridge-ai` to `aa19de3a7427f84a5279a2e1844f0eaba1c754b4`
  (BFF proxies, directory `evidenced` flag, register / card / gateway UI).
- Restarts `a2a-exchange` and `settlebridge`.
- Rebuilds and restarts `settlebridge-marketplace`.
- Sets `is_test = true` on existing accounts whose names start with
  `Smoke`, `Sybil`, `Cold`, `probe`, `Assist`, or `Trust` (case-sensitive
  prefix). Does not change `status`, balances, or delete rows. Writes the
  affected account IDs to `/root/plan45-is-test-backfill.txt` so rollback
  can set those rows back to `is_test = false`.
- Does **not** change `AGENT_MANAGEMENT_V2` (plan 44 already turned it on).
- Does **not** print `.env` values, tokens, or keys.
- Does **not** touch settlement, escrow, or reputation scoring code paths
  beyond deploying the already-reviewed commits.

Pinned commits:

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| a2a-settlement | `/home/clawdbot/a2a-settlement` | `6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a` |
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `aa19de3a7427f84a5279a2e1844f0eaba1c754b4` |

Expected heads before this plan (P.1 records the actual values):

| Repo | Acceptable current HEAD |
|------|-------------------------|
| a2a-settlement | `3be803f416cf3bc822411f49c83571c8801c3b74` or `f681ba593e066455d21e38b164997eb015d0dd05` |
| settlebridge-ai | `794ccda54de66ea96ccef40cbd1dc2abc21ceb8d` |

## Rules (same as plans 1–44)

- Run blocks as `root`, one block at a time: wait for each block to finish and
  check its result before starting the next. Each uses `set -euo pipefail` and
  stops on the first failure. If any command or `test` fails, **stop and
  report** the failing line and its output. Do not work around it.
- Never print `.env` values, tokens, or keys. Don't pipe secrets into
  `tee /dev/stderr`.
- `git status` on each repo must be clean (or only expected dirt) before
  pull. If unexpected dirty files block ff-only, stop and report.
- Run git / npm as `clawdbot`.
- One SSH session. Do not open a burst of new SSH connections.

## P.1 Pre-flight (read-only) and test-cohort preview

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
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:3001/agents | grep -qx 200

EX_HEAD=$(git -C "$EX" rev-parse HEAD)
SB_HEAD=$(git -C "$SB" rev-parse HEAD)
echo "EX_HEAD=$EX_HEAD"
echo "SB_HEAD=$SB_HEAD"
case "$EX_HEAD" in
  3be803f416cf3bc822411f49c83571c8801c3b74|f681ba593e066455d21e38b164997eb015d0dd05) ;;
  *) echo "unexpected exchange HEAD"; exit 1 ;;
esac
test "$SB_HEAD" = "794ccda54de66ea96ccef40cbd1dc2abc21ceb8d"
printf '%s\n' "$EX_HEAD" > /root/plan45-prev-exchange.sha
printf '%s\n' "$SB_HEAD" > /root/plan45-prev-settlebridge.sha

curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401
test "$(xe "select to_regclass('public.developer_id_owners')")" = "developer_id_owners"
test "$(xe "select count(*) from information_schema.columns where table_name='accounts' and column_name='is_test'")" = 1
curl -s -m 10 http://127.0.0.1:8002/api/config | grep -Eq '"agent_management_v2":[[:space:]]*true'

cd "$SB"
git status -sb | head -20
test -f marketplace-next/.next/standalone/server.js

if grep -qE '<[A-Z]+_(PREV_)?SHA>' /root/deploy-plans/2026-11-15-agent-management-phase2/AGENT_MANAGEMENT_PHASE2_PLAN.md; then
  echo "PLAN STILL HAS PLACEHOLDER SHAS — stop"; exit 1
fi

# Case-sensitive name prefix. Read-only. Prints id, status, is_test, bot_name.
xe "SELECT id || E'\t' || status || E'\t' || is_test::text || E'\t' || bot_name FROM accounts WHERE bot_name ~ '^(Smoke|Sybil|Cold|probe|Assist|Trust)' ORDER BY bot_name" \
  | tee /root/plan45-cohort-preview.tsv
echo "COHORT_COUNT=$(wc -l < /root/plan45-cohort-preview.tsv)"
echo "COHORT_NAMES:"
cut -f4 /root/plan45-cohort-preview.tsv
```

Record `EX_HEAD` and `SB_HEAD` in your report, plus `COHORT_COUNT` and the names.
Stop here if the count or names look wrong. Do not continue to P.2 until you
accept the preview.

## P.2a Deploy exchange

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
cd "$EX"
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  sudo -u clawdbot git stash push -u -m "plan45-ex-pre-deploy-dirt" -- docs-site site migrate_to_postgres.py || true
fi
sudo -u clawdbot git merge --ff-only 6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a
echo "EX_NOW=$(git rev-parse --short HEAD) $(git log -1 --oneline)"
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

## P.2b Deploy SettleBridge code

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
cd "$SB"
git status
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  sudo -u clawdbot git stash push -u -m "plan45-pre-deploy-dirt" || true
fi
sudo -u clawdbot git merge --ff-only aa19de3a7427f84a5279a2e1844f0eaba1c754b4
echo "SB_NOW=$(git rev-parse --short HEAD) $(git log -1 --oneline)"

if [[ -x "$SB/backend/.venv/bin/python" ]] && "$SB/backend/.venv/bin/python" -c "import pytest" 2>/dev/null; then
  sudo -u clawdbot bash -lc "cd $SB/backend && .venv/bin/python -m pytest tests/test_bots_ownership.py tests/test_bots_routes.py tests/test_skill_evidence.py -q"
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
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:3001/agents || true)
  [ "$code" = 200 ] && break
  sleep 1
done
test "$code" = 200
echo "MARKETPLACE OK"
```

If the memory check fails: start `settlebridge-marketplace` again, **stop and
report**. Do not force a build under memory pressure.

## P.4 Backfill is_test on the previewed cohort

Non-destructive. Sets `is_test = true` only. Does not change `status`,
balances, or delete rows. Only rows that were `is_test = false` are listed
in `/root/plan45-is-test-backfill.txt`.

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -v ON_ERROR_STOP=1 "$@"; }
test -f /root/plan45-cohort-preview.tsv
OUT=/root/plan45-is-test-backfill.txt
xe -Atc "UPDATE accounts SET is_test = true WHERE bot_name ~ '^(Smoke|Sybil|Cold|probe|Assist|Trust)' AND is_test = false RETURNING id;" \
  | tee "$OUT"
chmod 600 "$OUT"
echo "BACKFILL_ROWS=$(grep -c . "$OUT" || true)"

xe -At -F "$(printf '\t')" -c "SELECT id, status, is_test::text, bot_name FROM accounts WHERE bot_name ~ '^(Smoke|Sybil|Cold|probe|Assist|Trust)' ORDER BY id" \
  > /root/plan45-cohort-after.tsv
awk -F '\t' '{print $1 "\t" $2}' /root/plan45-cohort-preview.tsv | sort > /tmp/plan45-status-before
awk -F '\t' '{print $1 "\t" $2}' /root/plan45-cohort-after.tsv | sort > /tmp/plan45-status-after
diff -u /tmp/plan45-status-before /tmp/plan45-status-after
awk -F '\t' '$3 != "true" { print "still not is_test:", $0; bad=1 } END { exit bad+0 }' /root/plan45-cohort-after.tsv
echo "COHORT STATUS UNCHANGED AND is_test true"
```

`diff` must be empty. If it prints any line, status changed: **stop** and
run P.6.

## P.5 Smoke (no keys printed)

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
  "$MK/dashboard" \
  "$MK/dashboard/settings"
do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u" || true)
  [ "$code" = 200 ] || [ "$code" = 307 ] || [ "$code" = 308 ] && ok "$code $u" || bad "$code $u"
done

curl -s -m 10 "$MK/api/config" | grep -Eq '"agent_management_v2":[[:space:]]*true' \
  && ok "api/config agent_management_v2=true" \
  || bad "api/config missing agent_management_v2=true"

code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST http://127.0.0.1:8000/v1/accounts/developer-ids/attach -H 'content-type: application/json' -d '{"agent_api_key":"ate_not_a_real_key"}' || true)
[ "$code" = 401 ] || [ "$code" = 403 ] && ok "$code attach unauth" || bad "$code attach unauth"

code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/does-not-exist/settlements || true)
[ "$code" = 401 ] || [ "$code" = 403 ] && ok "$code settlements unauth" || bad "$code settlements unauth"

code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST http://127.0.0.1:8002/api/bots/register -H 'content-type: application/json' -d '{}' || true)
[ "$code" = 401 ] || [ "$code" = 403 ] && ok "$code bff register unauth" || bad "$code bff register unauth"

for needle in "Add Developer ID" "Publish Agent Card" "Evidenced" "Gateway"; do
  grep -rq "$needle" "$M/.next/standalone/.next/static/chunks" \
    && ok "bundle includes $needle" \
    || bad "bundle missing $needle"
done

test "$FAIL" -eq 0
echo "PLAN 45 SMOKE OK"
```

## P.6 Rollback

Run only if a later block failed or you are reverting this plan. Restores
`is_test` for the IDs written by P.4, then returns both repos to the SHAs
saved in P.1 and rebuilds the marketplace. Does not turn the feature flag off.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PG="docker exec a2a-settlement-postgres-1"
OUT=/root/plan45-is-test-backfill.txt
if [ -s "$OUT" ]; then
  if grep -vE '^[A-Za-z0-9_-]+$' "$OUT" | grep -q .; then
    echo "backfill file has an unexpected id"; exit 1
  fi
  list=$(sed "s/^/'/;s/$/'/" "$OUT" | paste -sd, -)
  $PG psql -U a2a -d a2a_exchange -v ON_ERROR_STOP=1 -c "UPDATE accounts SET is_test = false WHERE id IN ($list);"
  echo "is_test restored for backfill ids"
else
  echo "no backfill ids to restore"
fi

EX_PREV=$(cat /root/plan45-prev-exchange.sha)
SB_PREV=$(cat /root/plan45-prev-settlebridge.sha)
cd "$EX"
sudo -u clawdbot git reset --hard "$EX_PREV"
cd "$SB"
sudo -u clawdbot git reset --hard "$SB_PREV"
systemctl restart a2a-exchange settlebridge
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
for u in a2a-exchange settlebridge settlebridge-marketplace; do
  for i in $(seq 1 30); do systemctl is-active --quiet $u && break; sleep 1; done
  systemctl is-active $u
done
```

## Done when

- Exchange HEAD is `6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a`
- SettleBridge HEAD is `aa19de3a7427f84a5279a2e1844f0eaba1c754b4`
- P.4 reports cohort status unchanged and `is_test` true
- `/root/plan45-is-test-backfill.txt` exists (empty only if every matching row was already `is_test`)
- P.5 prints `PLAN 45 SMOKE OK`
- `AGENT_MANAGEMENT_V2` is still true (do not print the env file)
