# Droplet plan 46: is_test cohort 2

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-15.

**Depends on plan 45 (already the production pin):**

- Exchange at `6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a`
- SettleBridge code at `aa19de3a7427f84a5279a2e1844f0eaba1c754b4`
- `accounts.is_test` exists. Plan 45 set `is_test = true` for the
  `Smoke|Sybil|Cold|probe|Assist|Trust` name cohort and wrote those ids to
  `/root/plan45-is-test-backfill.txt`. Status was not changed.

**This plan:**

- Fast-forwards `a2a-settlement` to `3ce5e634a3e396990b9c686044cb3ce51195265a`
  (card registration under a claimed `developer_id` requires the owner key).
- Fast-forwards `settlebridge-ai` to `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1`
  (My agents defaults to active rows, suspended rows use one toggle, auth
  loading does not flash the signed-out state, dashboard counts skip the
  operator's linked account). That commit's parent is the docs commit
  `b1795390e09cdda8a95fb65f0c9eac3169a05e4c`. The fast-forward target is
  `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1`, not a later commit that only
  adds this plan file.
- Restarts `a2a-exchange` and `settlebridge`.
- Rebuilds and restarts `settlebridge-marketplace` (memory check
  `free -m` available >= 1150, build as `clawdbot`, copy standalone static).
- Sets `is_test = true` where `is_test = false` and `bot_name` matches the
  case-sensitive POSIX prefix
  `^(branreq-|bransrv-|qqq-|test-probe-|Smoke|Sybil|Cold|probe)`.
  `Assist` and `Trust` are not included. A re-run of the predicate is
  idempotent: rows plan 45 already marked stay `is_test = true` and are not
  updated again. Does not change `status`, balances, or delete rows.
- Writes only the updated account ids to `/root/plan46-is-test-backfill.txt`
  (mode 600) so rollback can set those rows back to `is_test = false`.
- Does **not** change `AGENT_MANAGEMENT_V2`.
- Does **not** print `.env` values, tokens, or keys.

Pinned commits (deploy these, not a later docs-only tip):

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| a2a-settlement | `/home/clawdbot/a2a-settlement` | `3ce5e634a3e396990b9c686044cb3ce51195265a` |
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1` |

Expected heads before this plan (P.1 records the actual values):

| Repo | Acceptable current HEAD |
|------|-------------------------|
| a2a-settlement | `6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a` |
| settlebridge-ai | `aa19de3a7427f84a5279a2e1844f0eaba1c754b4` or `b1795390e09cdda8a95fb65f0c9eac3169a05e4c` |

## Rules (same as plans 1–45)

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
- P.1 is read-only. Stop after P.1 for a human to accept the preview.
  Do not run the deploy blocks or P.2 until that review is done.
- P.2 is the only `UPDATE`. It runs after the deploy blocks.

## P.1 Pre-flight (read-only) and cohort preview

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PG="docker exec a2a-settlement-postgres-1"
PATTERN='^(branreq-|bransrv-|qqq-|test-probe-|Smoke|Sybil|Cold|probe)'

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
test "$EX_HEAD" = "6a1b4fbd705e5ecd45fbc2b5a1fad4df7a19876a"
case "$SB_HEAD" in
  aa19de3a7427f84a5279a2e1844f0eaba1c754b4|b1795390e09cdda8a95fb65f0c9eac3169a05e4c) ;;
  *) echo "unexpected settlebridge HEAD"; exit 1 ;;
esac
printf '%s\n' "$EX_HEAD" > /root/plan46-prev-exchange.sha
printf '%s\n' "$SB_HEAD" > /root/plan46-prev-settlebridge.sha

curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401
test "$( $PG psql -U a2a -d a2a_exchange -q -Atc "select count(*) from information_schema.columns where table_name='accounts' and column_name='is_test'" )" = 1
curl -s -m 10 http://127.0.0.1:8002/api/config | grep -Eq '"agent_management_v2":[[:space:]]*true'

cd "$SB"
git status -sb | head -20
test -f marketplace-next/.next/standalone/server.js

# Read-only. Columns: id, status, is_test, bot_name. Case-sensitive prefix.
$PG psql -U a2a -d a2a_exchange -q -At -F "$(printf '\t')" -c \
  "SELECT id, status, is_test::text, bot_name FROM accounts WHERE bot_name ~ '${PATTERN}' ORDER BY id" \
  > /root/plan46-cohort-preview.tsv
echo "COHORT_COUNT=$(wc -l < /root/plan46-cohort-preview.tsv)"
echo "WILL_UPDATE=$(awk -F '\t' '$3=="false" {n++} END {print n+0}' /root/plan46-cohort-preview.tsv)"
echo "ALREADY_IS_TEST=$(awk -F '\t' '$3=="true" {n++} END {print n+0}' /root/plan46-cohort-preview.tsv)"
echo "COHORT_NAMES:"
cut -f4 /root/plan46-cohort-preview.tsv
echo "COHORT_DETAIL status, is_test, name:"
cut -f2,3,4 /root/plan46-cohort-preview.tsv
```

Record `EX_HEAD`, `SB_HEAD`, `COHORT_COUNT`, `WILL_UPDATE`, and the names,
status, and `is_test` values. Stop here if the count or names look wrong.
Do not continue until you accept the preview. Rows already `is_test = true`
are listed so a missed plan-45 name is visible; P.2 will not update them.

## D.1 Deploy exchange

Run only after the P.1 preview is accepted.

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
cd "$EX"
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  echo "dirty exchange tree; stashing before ff-only"
  sudo -u clawdbot git stash push -u -m "plan46-ex-pre-deploy-dirt" || true
fi
sudo -u clawdbot git merge --ff-only 3ce5e634a3e396990b9c686044cb3ce51195265a
test "$(git rev-parse HEAD)" = "3ce5e634a3e396990b9c686044cb3ce51195265a"
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

Fast-forward to the feature commit. Do not fast-forward to a later commit
whose only change is this plan file.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
cd "$SB"
git status
git fetch origin
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  echo "dirty settlebridge tree; stashing before ff-only"
  sudo -u clawdbot git stash push -u -m "plan46-pre-deploy-dirt" || true
fi
sudo -u clawdbot git merge --ff-only 2551ea4abce87c6b49695566b7b2ea6e88e7cbe1
test "$(git rev-parse HEAD)" = "2551ea4abce87c6b49695566b7b2ea6e88e7cbe1"
echo "SB_NOW=$(git rev-parse HEAD) $(git log -1 --oneline)"

if [[ -x "$SB/backend/.venv/bin/python" ]] && "$SB/backend/.venv/bin/python" -c "import pytest" 2>/dev/null; then
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
report**. Do not force a build under memory pressure. Do not run P.2.

## D.4 Smoke (no keys printed)

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
  [ "$code" = 200 ] || [ "$code" = 307 ] || [ "$code" = 308 ] && ok "$code $u" || bad "$code $u"
done

curl -s -m 10 "$MK/api/config" | grep -Eq '"agent_management_v2":[[:space:]]*true' \
  && ok "api/config agent_management_v2=true" \
  || bad "api/config missing agent_management_v2=true"

grep -rq "Show suspended" "$M/.next/standalone/.next/static/chunks" \
  && ok "bundle includes Show suspended" \
  || bad "bundle missing Show suspended"

test "$FAIL" -eq 0
echo "PLAN 46 SMOKE OK"
```

## P.2 Backfill is_test on the previewed cohort

Run only after D.1–D.4 succeed and the P.1 preview is still the cohort you
accepted. Non-destructive. Sets `is_test = true` only, and only where it is
still false. Does not change `status`, balances, or delete rows.

`psql -q -At` is required so the command tag `UPDATE n` is not written into
the id file. The file must contain account ids only. If
`/root/plan46-is-test-backfill.txt` already exists and is non-empty, stop.
Do not overwrite the rollback list.

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
PATTERN='^(branreq-|bransrv-|qqq-|test-probe-|Smoke|Sybil|Cold|probe)'
test -f /root/plan46-cohort-preview.tsv
OUT=/root/plan46-is-test-backfill.txt
if [ -s "$OUT" ]; then
  echo "backfill id file already exists; not overwriting"
  exit 1
fi
$PG psql -U a2a -d a2a_exchange -q -At -v ON_ERROR_STOP=1 -c \
  "UPDATE accounts SET is_test = true WHERE is_test = false AND bot_name ~ '${PATTERN}' RETURNING id;" \
  > "$OUT"
chmod 600 "$OUT"
test "$(stat -c %a "$OUT")" = "600"
if [ -s "$OUT" ] && grep -vE '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "$OUT" | grep -q .; then
  echo "id file contains a non-id line"
  exit 1
fi
echo "BACKFILL_ROWS=$(grep -cE '^[0-9a-fA-F-]{36}$' "$OUT" || true)"

$PG psql -U a2a -d a2a_exchange -q -At -F "$(printf '\t')" -c \
  "SELECT id, status, is_test::text, bot_name FROM accounts WHERE bot_name ~ '${PATTERN}' ORDER BY id" \
  > /root/plan46-cohort-after.tsv
awk -F '\t' '{print $1 "\t" $2}' /root/plan46-cohort-preview.tsv | sort > /tmp/plan46-status-before
awk -F '\t' '{print $1 "\t" $2}' /root/plan46-cohort-after.tsv | sort > /tmp/plan46-status-after
diff -u /tmp/plan46-status-before /tmp/plan46-status-after
awk -F '\t' '$3 != "true" { print "still not is_test:", $0; bad=1 } END { exit bad+0 }' /root/plan46-cohort-after.tsv
echo "COHORT STATUS UNCHANGED AND is_test true"
```

`diff` must be empty. If it prints any line, status changed: **stop** and
run P.3. Matched rows (every name in the preview) must be `is_test` true.

## P.3 Rollback

Run only if a later block failed or you are reverting this plan. Sets
`is_test = false` only for account ids in `/root/plan46-is-test-backfill.txt`.
Blank lines and any non-id line are skipped. Does not change `status`.

This plan also deploys application code, so rollback returns both repos to
the SHAs saved in P.1 and rebuilds the marketplace. Do not print `.env`.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PG="docker exec a2a-settlement-postgres-1"
OUT=/root/plan46-is-test-backfill.txt
if [ -s "$OUT" ]; then
  grep -E '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "$OUT" > /tmp/plan46-rollback-ids.txt || true
  if [ -s /tmp/plan46-rollback-ids.txt ]; then
    list=$(sed "s/^/'/;s/$/'/" /tmp/plan46-rollback-ids.txt | paste -sd, -)
    $PG psql -U a2a -d a2a_exchange -q -v ON_ERROR_STOP=1 -c \
      "UPDATE accounts SET is_test = false WHERE id IN ($list);"
    echo "is_test restored for backfill ids"
  else
    echo "no account ids to restore"
  fi
else
  echo "no backfill ids to restore"
fi

test -f /root/plan46-prev-exchange.sha
test -f /root/plan46-prev-settlebridge.sha
EX_PREV=$(cat /root/plan46-prev-exchange.sha)
SB_PREV=$(cat /root/plan46-prev-settlebridge.sha)
cd "$EX"
sudo -u clawdbot git reset --hard "$EX_PREV"
cd "$SB"
sudo -u clawdbot git reset --hard "$SB_PREV"
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
systemctl restart settlebridge-marketplace
for u in a2a-exchange settlebridge settlebridge-marketplace; do
  for i in $(seq 1 30); do systemctl is-active --quiet $u && break; sleep 1; done
  systemctl is-active $u
done
```

## Done when

- Exchange HEAD is `3ce5e634a3e396990b9c686044cb3ce51195265a`
- SettleBridge HEAD is `2551ea4abce87c6b49695566b7b2ea6e88e7cbe1`
- P.2 reports cohort status unchanged and matched rows `is_test` true
- `/root/plan46-is-test-backfill.txt` exists, mode 600, and contains only
  account ids (empty only if every matching row was already `is_test`)
- D.4 prints `PLAN 46 SMOKE OK`
- `AGENT_MANAGEMENT_V2` is still true (do not print the env file)
