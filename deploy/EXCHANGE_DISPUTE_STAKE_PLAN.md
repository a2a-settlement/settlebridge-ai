# Droplet plan 51: exchange dispute-stake fix (a2a-settlement PR #16)

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-17 after
[a2a-settlement PR #16](https://github.com/a2a-settlement/a2a-settlement/pull/16) merged.

Independent of plan 50. A read-only check on 2026-11-17 found the exchange
checkout `/home/clawdbot/a2a-settlement` on `8149a0d`, branch `main`, with no
staged, unstaged, or untracked changes. `a2a_exchange` has 161 escrows (28
expired, 52 refunded, 81 released), none disputed, and no escrow has ever
carried a dispute stake: there are no `dispute_stake_*` transactions.

**Why.** Correctness fix. When a dispute ended without a mediator ruling, the
filer's stake stayed in `held_in_escrow` on a terminal escrow with nothing to
recover it. The exchange now returns the stake:

- when the sweep expires a dispute or applies a default judgment, and only
  after that sweep's claim on the escrow succeeded, so two overlapping sweeps
  cannot return it twice;
- when an operator force-refunds a disputed escrow or overrides a dispute
  from the dashboard.

Production has no staked disputes today, so no existing row changes. This
closes the hole before one is filed.

**This plan:**

- Backs up the exchange env file and marks the current commit with a branch.
- Fast-forwards `8149a0d` → `49678d589071251d2440034080436399fe9ce88b`
  (12 files). Two are exchange code: `exchange/observers.py` and
  `exchange/routes/dashboard.py`. Three are tests. The rest are docs and
  config that the running service does not read: `.gitignore`,
  `DEPLOYMENT.md`, `README.md`, `STATUS.md`, `deploy/Caddyfile.exchange`,
  `deploy/a2a-exchange.service`, `docker-compose.yml`.
- Restarts `a2a-exchange` (a few seconds of 502 for SettleBridge, mediator
  and MCP calls to the exchange).

**This plan does not:**

- Change the schema, the dependencies, or any `.env` or env file.
- Run `docker compose`. `docker-compose.yml` changes on disk, but only in the
  `mediator` service block, which production does not use. The Postgres
  container `a2a-settlement-postgres-1` was started from this file and is
  left running untouched; D.3 checks it was not restarted.
- Install `deploy/a2a-exchange.service` or `deploy/Caddyfile.exchange`. They
  are reference copies of what is already on the host.
- Change any existing escrow, balance, or transaction row.

## Rules (same as plans 1–50)

- Run blocks as `root`, one block at a time. Wait for each block to finish
  and check its result before starting the next. Each block uses
  `set -euo pipefail` and stops on the first failure. If any command or
  `test` fails, **stop and report** the failing line and its output. Do not
  work around it.
- Run git as `clawdbot`. Fetch over public HTTPS.
- Never print env values, tokens or keys.
- One SSH session. Do not open a burst of new SSH connections.
- D.5 is rollback only. Do not run it unless D.3 or D.4 shows a broken
  exchange, or the user asks.

## D.1 Pre-flight (read-only)

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
g() { sudo -u clawdbot git -C "$@"; }
test "$(g $EX rev-parse HEAD)" = 8149a0daf7ea5a95d7d58728829601003d895e32
test "$(g $EX rev-parse --abbrev-ref HEAD)" = main
test -z "$(g $EX status --porcelain)"
test -f /etc/a2a-exchange/env
for u in a2a-exchange a2a-mediator a2a-mcp settlebridge caddy; do systemctl is-active --quiet $u; done
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
test "$(xe "select count(*) from (select escrow_id from transactions where tx_type='dispute_stake_return' group by escrow_id having count(*)>1) d")" = 0
test "$(xe "select count(*) from balances where held_in_escrow < 0")" = 0
echo "escrows by status:"; xe "select status, count(*) from escrows group by status order by status"
echo "escrows holding a stake: $(xe "select count(*) from escrows where dispute_stake_status = 'held'")"
echo "postgres started: $(docker inspect -f '{{.State.StartedAt}}' a2a-settlement-postgres-1)"
echo "PRE-FLIGHT OK"
```

If `escrows holding a stake` is not 0, a staked dispute was filed after this
plan was written. That is not a failure, but **report the number before
D.3**: after the deploy, the next sweep or operator action on those escrows
will return the stake.

## D.2 Back up the env file and the current commit

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
g() { sudo -u clawdbot git -C "$@"; }
TS=$(date -u +%Y%m%dT%H%M%SZ); B=/home/clawdbot/backups/exchange-dispute-stake-$TS
install -d -o clawdbot -g clawdbot -m 700 $B
cp -p /etc/a2a-exchange/env $B/a2a-exchange.env
test -s $B/a2a-exchange.env
docker inspect -f '{{.State.StartedAt}}' a2a-settlement-postgres-1 > $B/postgres-started-at
echo "$B" > /home/clawdbot/backups/exchange-dispute-stake-current
g $EX branch backup/pre-dispute-stake-$TS 8149a0daf7ea5a95d7d58728829601003d895e32
ls -l $B | awk '{print $1, $5, $NF}'
echo "BACKUP OK ($B; branch backup/pre-dispute-stake-$TS)"
```

## D.2a Give the checkout back to `clawdbot` (added during the run)

The first run of D.3 stopped at the fetch with `insufficient permission for
adding an object to repository database .git/objects`. Nothing had changed:
the checkout was still on `8149a0d`, clean, and the exchange was up. Earlier
work run as `root` had left 211 root-owned entries under `.git` (three object
directories among them, which is what blocked the fetch) and 16 root-owned
source files and directories, including `exchange/routes/dashboard.py`.

This block changes ownership only, for everything in the checkout outside
`.venv`. It changes no file contents and no modes. `.venv` is left alone.

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
g() { sudo -u clawdbot git -C "$@"; }
test "$(g $EX rev-parse HEAD)" = 8149a0daf7ea5a95d7d58728829601003d895e32
BEFORE=$(find $EX -path $EX/.venv -prune -o ! -user clawdbot -print | wc -l)
find $EX -path $EX/.venv -prune -o ! -user clawdbot -exec chown -h clawdbot:clawdbot {} +
test "$(find $EX -path $EX/.venv -prune -o ! -user clawdbot -print | wc -l)" = 0
cd / && test "$(sudo -u clawdbot find $EX/.git -type d ! -writable | wc -l)" = 0
test "$(g $EX rev-parse HEAD)" = 8149a0daf7ea5a95d7d58728829601003d895e32
test -z "$(g $EX status --porcelain)"
systemctl is-active --quiet a2a-exchange
echo "OWNERSHIP OK ($BEFORE entries changed to clawdbot)"
```

## D.3 Fast-forward and restart

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
g() { sudo -u clawdbot git -C "$@"; }
OLD=8149a0daf7ea5a95d7d58728829601003d895e32
NEW=49678d589071251d2440034080436399fe9ce88b
B=$(cat /home/clawdbot/backups/exchange-dispute-stake-current)
g $EX fetch -q https://github.com/a2a-settlement/a2a-settlement.git main:refs/remotes/origin/main
g $EX merge-base --is-ancestor $NEW refs/remotes/origin/main
g $EX merge-base --is-ancestor $OLD $NEW
test "$(g $EX diff --name-only $OLD $NEW | LC_ALL=C sort | tr '\n' ';')" = ".gitignore;DEPLOYMENT.md;README.md;STATUS.md;deploy/Caddyfile.exchange;deploy/a2a-exchange.service;docker-compose.yml;exchange/observers.py;exchange/routes/dashboard.py;tests/test_dashboard_dispute_stake.py;tests/test_timeout_observer.py;tests/test_x1_reservation_settlement.py;"
# The compose change must not touch the postgres service production runs.
test -z "$(g $EX diff $OLD $NEW -- docker-compose.yml | grep '^[+-][^+-]' | grep -iE 'postgres|pgdata|5432' || true)"
test -z "$(g $EX status --porcelain)"
g $EX merge --ff-only -q $NEW
test "$(g $EX rev-parse HEAD)" = $NEW
test -z "$(g $EX status --porcelain)"
sudo -u clawdbot $EX/.venv/bin/python -c "import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]" $EX/exchange/observers.py $EX/exchange/routes/dashboard.py
grep -q '_return_dispute_stake' $EX/exchange/observers.py
grep -q '_return_dispute_stake' $EX/exchange/routes/dashboard.py

systemctl restart a2a-exchange
for i in $(seq 1 30); do systemctl is-active --quiet a2a-exchange && break; sleep 1; done
systemctl is-active a2a-exchange
code=000; for i in $(seq 1 20); do code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8000/health || true); [ "$code" = 200 ] && break; sleep 1; done
test "$code" = 200
sleep 5
journalctl -u a2a-exchange --since "-2 min" --no-pager | grep -iE 'traceback|exception' || echo "no tracebacks in journal"
for u in a2a-mediator a2a-mcp; do
  systemctl is-active --quiet $u || { echo "$u stopped with the exchange; starting it"; systemctl start $u; sleep 5; systemctl is-active $u; }
done
test "$(docker inspect -f '{{.State.StartedAt}}' a2a-settlement-postgres-1)" = "$(cat $B/postgres-started-at)"
echo "EXCHANGE OK at $NEW"
```

## D.4 Smoke tests (read-only)

```bash
set -uo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
FAIL=0
ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; FAIL=1; }
for u in http://127.0.0.1:8000/health http://127.0.0.1:8002/health http://127.0.0.1:3100/health https://exchange.a2a-settlement.org/health https://market.settlebridge.ai/bounties; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 $u); [ "$code" = 200 ] && ok "200 $u" || bad "$code $u"
done
# The two changed dashboard routes are mounted and still demand credentials.
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST http://127.0.0.1:8000/v1/dashboard/escrows/plan51-smoke/force-refund)
[ "$code" = 401 ] && ok "401 force-refund without credentials" || bad "$code force-refund without credentials"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST http://127.0.0.1:8000/v1/disputes/dispute-plan51-smoke/resolve -H 'content-type: application/json' -d '{"resolution":"refund"}')
[ "$code" = 401 ] && ok "401 dispute override without credentials" || bad "$code dispute override without credentials"
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"plan51","version":"0"}}}'
curl -s -m 10 -X POST http://127.0.0.1:3200/mcp -H 'Host: 127.0.0.1:3200' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT" | grep -q '"serverInfo"' && ok "MCP initialize" || bad "MCP initialize"
N=$(xe "select count(*) from (select escrow_id from transactions where tx_type='dispute_stake_return' group by escrow_id having count(*)>1) d")
[ "$N" = 0 ] && ok "no stake returned twice" || bad "$N escrows with more than one stake return"
N=$(xe "select count(*) from balances where held_in_escrow < 0")
[ "$N" = 0 ] && ok "no negative held balance" || bad "$N balances with negative held_in_escrow"
for u in a2a-exchange a2a-mediator a2a-mcp mcp-trust-gateway settlebridge settlebridge-marketplace caddy; do systemctl is-active --quiet $u && ok "$u active" || bad "$u"; done
journalctl -u a2a-exchange --since "-3 min" --no-pager | grep -iqE 'traceback|exception' && bad "tracebacks in the exchange journal" || ok "no tracebacks in the exchange journal"
[ $FAIL = 0 ] && echo "SMOKE OK" || echo "SMOKE FAILED: report before going further"
```

## D.5 Rollback (only if D.3–D.4 show a broken exchange)

The checkout is clean before and after, so a plain reset is enough. The
rollback changes no database rows: the new code only adds stake-return
transactions for staked disputes, and production has none.

```bash
set -euo pipefail
EX=/home/clawdbot/a2a-settlement
g() { sudo -u clawdbot git -C "$@"; }
OLD=8149a0daf7ea5a95d7d58728829601003d895e32
test -z "$(g $EX status --porcelain)"
g $EX reset -q --keep $OLD
test "$(g $EX rev-parse HEAD)" = $OLD
systemctl restart a2a-exchange; sleep 5; systemctl is-active a2a-exchange
curl -s -o /dev/null -w '%{http_code} exchange health\n' -m 10 http://127.0.0.1:8000/health
for u in a2a-mediator a2a-mcp; do systemctl is-active --quiet $u || systemctl start $u; done
echo "ROLLED BACK"
```

## Done when

- D.3 printed `EXCHANGE OK at 49678d5…` and D.4 printed `SMOKE OK`.
- `/home/clawdbot/a2a-settlement` is on `49678d5`, branch `main`, clean.
- Report the D.1 escrow counts, the backup path, and whether `a2a-mediator`
  or `a2a-mcp` had to be started after the restart.

## Not covered by this plan

- The fix was tested on SQLite. The overlapping-sweep case relies on the
  claim gate there; on Postgres the row lock already serialises sweeps. It
  has not been exercised on Postgres.
- The dashboard override always returns the stake to the filer. It has no
  forfeit option, unlike the mediator's resolve endpoint.

## Run record

Run 2026-11-18 (UTC).

- D.1 passed: 161 escrows (28 expired, 52 refunded, 81 released), none
  holding a stake.
- D.2 passed: backup at
  `/home/clawdbot/backups/exchange-dispute-stake-20261118T010955Z`, branch
  `backup/pre-dispute-stake-20261118T010955Z`.
- D.3 failed at the fetch on the first attempt, before any change. D.2a was
  added and run (225 entries changed to `clawdbot`), then D.3 passed.
- D.4 passed: `SMOKE OK`, all 18 checks.
- `a2a-mediator` and `a2a-mcp` stayed up through the restart. Postgres was
  not restarted. D.5 was not run.
- Left as is: about 380 root-owned files under `.venv`. A later plan that
  installs dependencies as `clawdbot` will need them fixed first.
