# Droplet plan 48: close the legacy_open Developer ID claim gap

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-15.

**Depends on plan 47 (already finished; rollback was not run):**

- Exchange at `2a2e2bca9171cb8a1914887a99d13298b24a0a22`
- SettleBridge at `f916cd5c5b4782b971b7e0ff82d197b214811ed9`
- `AGENT_MANAGEMENT_V2` is already true. Do not edit `.env`.

**This plan:**

- Fast-forwards `a2a-settlement` to `8149a0daf7ea5a95d7d58728829601003d895e32`.
  That range has two feature commits:
  - `4ddb31c8f67a578f84642d6554d56165c69f6bff` closes the `legacy_open` gap
    (rules below). Code only, no data migration.
  - `8149a0daf7ea5a95d7d58728829601003d895e32` adds the operator-only bind
    override `POST /v1/admin/developer-ids/{developer_id}/bind` and the
    additive audit table `developer_id_audit_events`.
- Creates `developer_id_audit_events` (additive). The exchange creates it at
  startup through its existing `create_all` path. D.2 verifies the table and
  creates it with `CREATE TABLE IF NOT EXISTS` only if startup did not.
- Fast-forwards `settlebridge-ai` to `747ae6fc474cb57ba1744c01681cf332d353df93`
  (Add Developer ID dialog copy: an unclaimed Developer ID needs the oldest
  agent's key). That range also contains `7c111c7` (plan 47 docs) and merged
  PR #52 (`e884f29`): a new standalone CLI
  `backend/app/services/fix_verifier_preflight.py`, its tests, and a docs
  file. Nothing in the app imports that module, so it changes nothing at
  runtime. The docs commit that adds this plan file is not a deploy pin. Do
  not fast-forward to it.
- Restarts `a2a-exchange`, `settlebridge`, and rebuilds and restarts
  `settlebridge-marketplace` the same way as plans 45–47.
- Smoke-checks the new 403 registration paths with requests the exchange
  refuses before inserting anything, and proves with read-only `psql` that no
  account, ownership row, or audit row was written.

**This plan does not:**

- Run an admin bind. Binding is a later step that the user approves. The
  section "Admin override reference" documents the call for that step only.
- Migrate, rewrite, or delete `legacy_open` or `claimed` rows in
  `developer_id_owners`.
- Create, change, or suspend accounts, keys, or Agent Cards.
- Change `is_test`, `AGENT_MANAGEMENT_V2`, or print `.env` values, tokens, or keys.

Pinned commits (deploy these, not a later docs-only tip):

| Repo | Droplet path | Deploy SHA |
|------|--------------|------------|
| a2a-settlement | `/home/clawdbot/a2a-settlement` | `8149a0daf7ea5a95d7d58728829601003d895e32` |
| settlebridge-ai | `/home/clawdbot/settlebridge-ai` | `747ae6fc474cb57ba1744c01681cf332d353df93` |

Expected heads before this plan (P.1 records the actual values and refuses
to continue on any other head):

| Repo | Required current HEAD |
|------|------------------------|
| a2a-settlement | `2a2e2bca9171cb8a1914887a99d13298b24a0a22` |
| settlebridge-ai | `f916cd5c5b4782b971b7e0ff82d197b214811ed9` or `e884f29bfc5948577a532dfe54b0961114f248ca` |

The droplet's SettleBridge checkout was found fast-forwarded to `e884f29`
(merged PR #52) outside a plan. That range only adds plan 47 docs and the
unimported `fix_verifier_preflight.py` CLI, and `e884f29` is an ancestor of
the plan 48 pin, so P.1 accepts either head. P.1 records whichever head is
present, and P.2 rolls back to it.

## Rules the exchange enforces after this plan

They apply to every `legacy_open` developer ID, at request time:

| Action | Condition | Result |
|--------|-----------|--------|
| `POST /v1/accounts/developer-ids/claim` | ID is `legacy_open` and caller is not its oldest agent (earliest `created_at`, ties by account `id`) | 403 |
| `POST /v1/accounts/developer-ids/claim` | caller is the oldest agent | 200, ID becomes `claimed` by caller |
| `POST /v1/accounts/developer-ids/attach` | `agent_api_key` belongs to a non-oldest agent of a `legacy_open` ID | 403 |
| `POST /v1/accounts/developer-ids/attach` | `agent_api_key` is the oldest agent's key | 200, ID becomes `claimed` by the caller |
| `GET /v1/accounts/managed` | caller is a non-oldest agent of its `legacy_open` primary ID | 200, no auto-claim |
| `POST /v1/accounts/register` and `POST /v1/accounts/register-agent` | ID is `legacy_open` and has at least one agent; no `owner_api_key`, or a key that is not an agent in that ID | 403 |
| same | `owner_api_key` (body, or `X-Owner-Api-Key` on register-agent) of any existing agent in that ID | 201, ID stays `legacy_open` (no auto-claim) |
| any of the above | no ownership row, or `legacy_open` with no agents | unchanged: registration open, first claimer wins |

Unchanged: claimed IDs (registration needs the owner's key, 403 otherwise),
attach of an ID claimed by another operator (403), second claimer (409),
invalid attach key (401).

Behavior changes to watch after deploy:

- SettleBridge GitHub signup registers the user's exchange account with
  `developer_id` set to the GitHub display name. If that name already
  matches a `legacy_open` ID that has agents, the exchange now returns 403,
  and the user gets no exchange account (a warning is logged). Before this
  plan, that user silently joined someone else's ID.
- `POST /api/gateway/agents/register` (utility-agent provisioning, needs the
  registration secret) cannot forward an owner key. A second agent under a
  `legacy_open` ID that already has agents now fails with 502 (wrapping the
  exchange 403). No repo in this workspace calls that endpoint.

## Rules (same as plans 1–47)

- Run blocks as `root`, one block at a time: wait for each block to finish and
  check its result before starting the next. Each uses `set -euo pipefail`
  (D.5 collects failures, then exits non-zero if any check failed) and stops
  on the first failure. If any command or `test` fails, **stop and report**
  the failing line and its output. Do not work around it.
- Never print `.env` values, tokens, or keys. Don't pipe secrets into
  `tee /dev/stderr`. Smoke responses go to a temp file that is grepped and
  deleted, never printed.
- `git status` on each repo must be clean (or only expected dirt) before
  pull. Stash only when dirt would block ff-only. Exchange: stash only
  `docs-site`, `site`, and `migrate_to_postgres.py`. SettleBridge:
  `git stash push -u`. If other exchange files are still dirty, stop and
  report. Do not stash unrelated exchange paths.
- Run git / npm as `clawdbot`.
- One SSH session. Do not open a burst of new SSH connections.
- Database reads use `psql -q -At`. The only database write in this plan is
  the additive `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
  in D.2, and only when startup did not already create the table.
- P.1 is read-only. It must succeed before D.1. If it exits, stop. Do not
  run D.1, D.2, D.3, D.4, D.5, or P.2.
- P.2 is rollback only. Do not run it unless a later block failed or you are
  reverting this plan.

## P.1 Pre-flight (read-only)

Refuses to continue unless both heads match plan 47. Reads counts only. Does
not write `developer_id_owners`, accounts, keys, or cards.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
PLAN=/root/deploy-plans/2026-11-15-agent-management-legacy-open/AGENT_MANAGEMENT_LEGACY_OPEN_PLAN.md
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }

systemctl is-active --quiet a2a-exchange
systemctl is-active --quiet settlebridge
systemctl is-active --quiet settlebridge-marketplace
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:3001/agents | grep -qx 200
docker ps --format '{{.Names}}' | grep -qx a2a-settlement-postgres-1

EX_HEAD=$(git -C "$EX" rev-parse HEAD)
SB_HEAD=$(git -C "$SB" rev-parse HEAD)
echo "EX_HEAD=$EX_HEAD"
echo "SB_HEAD=$SB_HEAD"
if [ "$EX_HEAD" != "2a2e2bca9171cb8a1914887a99d13298b24a0a22" ]; then
  echo "unexpected exchange HEAD; refusing to continue"
  exit 1
fi
if [ "$SB_HEAD" != "f916cd5c5b4782b971b7e0ff82d197b214811ed9" ] \
  && [ "$SB_HEAD" != "e884f29bfc5948577a532dfe54b0961114f248ca" ]; then
  echo "unexpected settlebridge HEAD; refusing to continue"
  exit 1
fi
printf '%s\n' "$EX_HEAD" > /root/plan48-prev-exchange.sha
printf '%s\n' "$SB_HEAD" > /root/plan48-prev-settlebridge.sha

curl -s -m 10 http://127.0.0.1:8002/api/config | grep -Eq '"agent_management_v2":[[:space:]]*true'
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/v1/accounts/managed | grep -qx 401

# Old code has no bind route.
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  http://127.0.0.1:8000/v1/admin/developer-ids/plan48-smoke-nonexistent/bind \
  -H 'content-type: application/json' \
  -d '{"owner_account_id":"plan48-smoke-nonexistent","reason":"pre-check"}' || true)
echo "bind route before deploy: $code (expect 404 or 405)"

echo "audit table before deploy: '$(xe "select coalesce(to_regclass('public.developer_id_audit_events')::text, '')")' (expect empty)"
echo "accounts: $(xe "select count(*) from accounts")"
echo "owner rows by status:"
xe "select status, count(*) from developer_id_owners group by status order by status"
echo "legacy_open IDs with >=1 agent: $(xe "select count(*) from (select o.developer_id from developer_id_owners o join accounts a on a.developer_id = o.developer_id where o.status = 'legacy_open' group by o.developer_id) t")"
echo "legacy_open IDs with >=2 agents (non-oldest members lose claim): $(xe "select count(*) from (select o.developer_id from developer_id_owners o join accounts a on a.developer_id = o.developer_id where o.status = 'legacy_open' group by o.developer_id having count(*) >= 2) t")"
echo "operator-status accounts (needed for a later bind): $(xe "select count(*) from accounts where status = 'operator'")"
test "$(xe "select count(*) from accounts where bot_name like 'plan48-smoke-%'")" = 0

cd "$SB"
git status -sb
test -f marketplace-next/.next/standalone/server.js

if grep -qE '[<][A-Z_]+[>]' "$PLAN"; then
  echo "PLAN STILL HAS PLACEHOLDERS — stop"
  exit 1
fi
echo "P.1 OK"
```

Record `EX_HEAD`, `SB_HEAD`, and the counts. Continue only when both heads
match and the block prints `P.1 OK`. If `operator-status accounts` is 0, the
deploy can still proceed; a later bind first needs an operator account,
which is a separate approved step.

## D.1 Deploy exchange

Run only after P.1 prints `P.1 OK`. Runs the ownership tests on the droplet
(SQLite in a temp dir, not the production database), then restarts.

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
  sudo -u clawdbot git stash push -u -m "plan48-ex-pre-deploy-dirt" -- docs-site site migrate_to_postgres.py
fi
if ! git diff --quiet || ! git diff --cached --quiet \
  || [ -n "$(git ls-files --others --exclude-standard | head -1)" ]; then
  echo "unexpected exchange dirt still blocks ff-only"
  git status -sb
  exit 1
fi
sudo -u clawdbot git merge --ff-only 8149a0daf7ea5a95d7d58728829601003d895e32
test "$(git rev-parse HEAD)" = "8149a0daf7ea5a95d7d58728829601003d895e32"
echo "EX_NOW=$(git rev-parse HEAD) $(git log -1 --oneline)"

sudo -u clawdbot "$EX/.venv/bin/python" -m pytest tests/test_managed_ownership.py -q -p no:cacheprovider

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

Expect `25 passed` from pytest. If pytest fails, stop before the restart
line runs (the block exits) and report. Then run P.2 only for the exchange
if you need to revert the checkout.

## D.2 Audit table (additive)

Startup normally creates `developer_id_audit_events`. This block verifies it
and creates it only if missing. It never drops or alters existing tables.

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }

if [ -z "$(xe "select coalesce(to_regclass('public.developer_id_audit_events')::text, '')")" ]; then
  echo "audit table missing after startup; creating it (additive)"
  $PG psql -U a2a -d a2a_exchange -q -v ON_ERROR_STOP=1 -c "CREATE TABLE IF NOT EXISTS developer_id_audit_events (
    id VARCHAR(36) NOT NULL,
    developer_id VARCHAR(255) NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor_account_id VARCHAR(36) NOT NULL,
    previous_status VARCHAR(20),
    previous_owner_account_id VARCHAR(36),
    new_status VARCHAR(20) NOT NULL,
    new_owner_account_id VARCHAR(36) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
  )"
  $PG psql -U a2a -d a2a_exchange -q -v ON_ERROR_STOP=1 -c "CREATE INDEX IF NOT EXISTS ix_developer_id_audit_events_developer_id ON developer_id_audit_events (developer_id)"
else
  echo "audit table created by startup"
fi

test "$(xe "select to_regclass('public.developer_id_audit_events')")" = "developer_id_audit_events"
test "$(xe "select count(*) from information_schema.columns where table_name = 'developer_id_audit_events'")" = 10
test "$(xe "select count(*) from information_schema.columns where table_name = 'developer_id_audit_events' and column_name like '%key%'")" = 0
test "$(xe "select count(*) from developer_id_audit_events")" = 0
echo "AUDIT TABLE OK"
```

## D.3 Deploy SettleBridge code

Fast-forward to the feature commit. The deploy pin is
`747ae6fc474cb57ba1744c01681cf332d353df93`, not the docs commit that adds
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
  sudo -u clawdbot git stash push -u -m "plan48-pre-deploy-dirt"
fi
sudo -u clawdbot git merge --ff-only 747ae6fc474cb57ba1744c01681cf332d353df93
test "$(git rev-parse HEAD)" = "747ae6fc474cb57ba1744c01681cf332d353df93"
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

## D.4 Build and restart marketplace

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
report**. Do not force a build under memory pressure. Do not run D.5.

## D.5 Smoke (no keys printed, no accounts created)

The register smokes target one existing `legacy_open` ID that has agents,
with bot names that do not exist and no valid owner key. The new code
refuses them with 403 inside the ownership check, before any insert. They
run only after the bind route answers 401, which proves the new code is
serving. Each refused register request uses one slot of the per-IP
registration rate limit for 127.0.0.1 (in memory, reset by the D.1 restart).

`oldest agent registered under it` is the Add Developer ID copy added in
`747ae6fc474cb57ba1744c01681cf332d353df93`. It is absent from
`f916cd5c5b4782b971b7e0ff82d197b214811ed9`.

```bash
set -uo pipefail
MK=https://market.settlebridge.ai
EXU=http://127.0.0.1:8000
M=/home/clawdbot/settlebridge-ai/marketplace-next
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
TMP=$(mktemp)
FAIL=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }

for u in \
  $EXU/health \
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
  $EXU/v1/accounts/developer-ids/attach \
  -H 'content-type: application/json' \
  -d '{"agent_api_key":"ate_not_a_real_key"}' || true)
[ "$code" = 401 ] || [ "$code" = 403 ] && ok "$code attach unauth" || bad "$code attach unauth"

BIND_BODY='{"owner_account_id":"plan48-smoke-nonexistent","reason":"smoke"}'
bind_unauth=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  $EXU/v1/admin/developer-ids/plan48-smoke-nonexistent/bind \
  -H 'content-type: application/json' -d "$BIND_BODY" || true)
[ "$bind_unauth" = 401 ] && ok "401 bind without auth" || bad "$bind_unauth bind without auth (expect 401)"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  $EXU/v1/admin/developer-ids/plan48-smoke-nonexistent/bind \
  -H 'content-type: application/json' -H 'Authorization: Bearer ate_not_a_real_key' \
  -d "$BIND_BODY" || true)
[ "$code" = 401 ] && ok "401 bind with bogus key" || bad "$code bind with bogus key (expect 401)"

DEV=$(xe "select o.developer_id from developer_id_owners o join accounts a on a.developer_id = o.developer_id where o.status = 'legacy_open' and o.developer_id not like '%/%' and o.developer_id not like '%''%' group by o.developer_id order by count(*) desc, o.developer_id limit 1")
if [ "$bind_unauth" != 401 ]; then
  bad "new exchange code not confirmed; register smokes skipped"
elif [ -z "$DEV" ]; then
  echo "skip register smokes: no legacy_open ID with agents"
else
  echo "register smokes target legacy_open developer_id: $DEV"
  OWN_BEFORE=$(xe "select status || '|' || coalesce(owner_account_id, '') from developer_id_owners where developer_id = '$DEV'")

  REG_BODY=$(python3 -c 'import json,sys; print(json.dumps({"bot_name":"plan48-smoke-refused-register","developer_id":sys.argv[1],"developer_name":"plan48 smoke","contact_email":"smoke@example.invalid"}))' "$DEV")
  code=$(curl -s -o "$TMP" -w '%{http_code}' -m 15 -X POST $EXU/v1/accounts/register \
    -H 'content-type: application/json' -d "$REG_BODY" || true)
  if [ "$code" = 403 ] && grep -q "already has agents" "$TMP"; then ok "403 register without owner key"
  elif grep -qi "invite code" "$TMP"; then echo "skip register: invite code gate answers first ($code)"
  else bad "$code register without owner key (expect 403 already has agents)"; fi

  REG_BODY=$(python3 -c 'import json,sys; print(json.dumps({"bot_name":"plan48-smoke-refused-register-key","developer_id":sys.argv[1],"developer_name":"plan48 smoke","contact_email":"smoke@example.invalid","owner_api_key":"ate_not_a_real_key"}))' "$DEV")
  code=$(curl -s -o "$TMP" -w '%{http_code}' -m 15 -X POST $EXU/v1/accounts/register \
    -H 'content-type: application/json' -d "$REG_BODY" || true)
  if [ "$code" = 403 ] && grep -q "already has agents" "$TMP"; then ok "403 register with non-member key"
  elif grep -qi "invite code" "$TMP"; then echo "skip register: invite code gate answers first ($code)"
  else bad "$code register with non-member key (expect 403 already has agents)"; fi

  CARD_BODY=$(python3 -c '
import json, sys
now = "2026-11-15T00:00:00+00:00"
print(json.dumps({
  "protocol_version": "2026.1", "name": "plan48-smoke-refused-card", "id": sys.argv[1],
  "description": "plan48 smoke", "kya_level": 0, "identity": {"type": "api_key"},
  "attestations": [], "settlement": {"supported_methods": ["escrow-v1"], "exchange_url": "http://127.0.0.1:8000"},
  "capabilities": {"skills": []}, "policies": {}, "metadata": {"created": now, "updated": now}}))' "$DEV")
  code=$(curl -s -o "$TMP" -w '%{http_code}' -m 15 -X POST $EXU/v1/accounts/register-agent \
    -H 'content-type: application/json' -H 'X-Owner-Api-Key: ate_not_a_real_key' -d "$CARD_BODY" || true)
  if [ "$code" = 403 ] && grep -q "already has agents" "$TMP"; then ok "403 register-agent with non-member key"
  elif grep -qi "invite code" "$TMP"; then echo "skip register-agent: invite code gate answers first ($code)"
  else bad "$code register-agent with non-member key (expect 403 already has agents)"; fi

  OWN_AFTER=$(xe "select status || '|' || coalesce(owner_account_id, '') from developer_id_owners where developer_id = '$DEV'")
  [ "$OWN_BEFORE" = "$OWN_AFTER" ] && ok "ownership row unchanged ($OWN_AFTER)" || bad "ownership row changed"
fi
rm -f "$TMP"

n=$(xe "select count(*) from accounts where bot_name like 'plan48-smoke-%'")
[ "$n" = 0 ] && ok "no smoke accounts created" || bad "$n smoke accounts exist — report, do not delete"
n=$(xe "select count(*) from developer_id_audit_events")
[ "$n" = 0 ] && ok "audit table empty (no bind ran)" || bad "$n audit rows — report"
n=$(xe "select count(*) from developer_id_owners where developer_id like 'plan48-smoke-%'")
[ "$n" = 0 ] && ok "no smoke ownership rows" || bad "$n smoke ownership rows"

grep -rq "oldest agent registered under it" "$M/.next/standalone/.next/static/chunks" \
  && ok "bundle includes new Add Developer ID copy" \
  || bad "bundle missing new Add Developer ID copy"

if [ "$FAIL" -ne 0 ]; then
  echo "PLAN 48 SMOKE FAILED"
  exit 1
fi
echo "PLAN 48 SMOKE OK"
```

## P.2 Rollback

Run only if a later block failed or you are reverting this plan. Resets both
repos to the SHAs saved in P.1 (the heads found before this plan) and rebuilds the
marketplace.

Leaves `developer_id_audit_events` in place. It is additive, and the plan 47
code ignores it. Do not drop it. This block does not change
`developer_id_owners`, accounts, keys, or cards. If an admin bind ran after
this plan (a later approved step), its `claimed` row stays claimed after
rollback, and plan 47 code honors it. Do not print `.env`.

```bash
set -euo pipefail
SB=/home/clawdbot/settlebridge-ai
EX=/home/clawdbot/a2a-settlement
echo "rollback resets git SHAs and rebuilds marketplace; no database writes"

test -f /root/plan48-prev-exchange.sha
test -f /root/plan48-prev-settlebridge.sha
EX_PREV=$(tr -d '[:space:]' < /root/plan48-prev-exchange.sha)
SB_PREV=$(tr -d '[:space:]' < /root/plan48-prev-settlebridge.sha)
test "$EX_PREV" = "2a2e2bca9171cb8a1914887a99d13298b24a0a22"
[ "$SB_PREV" = "f916cd5c5b4782b971b7e0ff82d197b214811ed9" ] \
  || [ "$SB_PREV" = "e884f29bfc5948577a532dfe54b0961114f248ca" ]

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

## Admin override reference (do not run in this plan)

For a later, separately approved bind step. Nothing in P.1–D.5 calls it.

Endpoint: `POST /v1/admin/developer-ids/{developer_id}/bind` (also served
under `/api/v1/...`). Auth header: `Authorization`, scheme `Bearer`, with the
API key of an exchange account whose `status` is `operator`. This is the same
gate as `/v1/accounts/admin/suspend` and `/v1/admin/kya/*`. The dashboard
token (`A2A_EXCHANGE_DASHBOARD_API_KEY` / `A2A_EXCHANGE_OPERATOR_API_KEY`) is
not accepted unless it is also the key of an operator-status account.

Request body (JSON):

- `owner_account_id` (string, required): exchange account id of the new owner.
- `reason` (string, required, 1–2000 chars, not blank): stored in the audit row.

Responses:

| Code | Meaning |
|------|---------|
| 200 | Bound. Body: `developer_id`, `owner_account_id`, `status` (`claimed`), `previous_status` (`legacy_open`, `claimed`, or null when no row existed), `previous_owner_account_id`, `audit_event_id` |
| 401 | Missing or invalid API key |
| 403 | Caller is not an operator-status account |
| 404 | `owner_account_id` does not exist |
| 409 | Already `claimed` by that same owner (no audit row written) |
| 422 | Missing or blank `reason`, or missing `owner_account_id` |

Each 200 writes exactly one row to `developer_id_audit_events`: `id`,
`developer_id`, `action` (`admin_bind`), `actor_account_id`,
`previous_status`, `previous_owner_account_id`, `new_status`,
`new_owner_account_id`, `reason`, `created_at`. It never stores a key.
Developer IDs that contain `/` cannot be bound through this path.

Invocation (fill `DEV_ID`, `OWNER_ID`, `REASON`; the key is read silently and
kept out of shell history and process arguments):

```bash
set -euo pipefail
DEV_ID=replace-with-developer-id
OWNER_ID=replace-with-owner-account-id
REASON="replace with the dispute ticket and decision"
read -rsp 'operator API key: ' OPERATOR_KEY; echo
HDR=$(mktemp); chmod 600 "$HDR"
printf 'Authorization: Bearer %s\n' "$OPERATOR_KEY" > "$HDR"
unset OPERATOR_KEY
BODY=$(python3 -c 'import json,sys; print(json.dumps({"owner_account_id": sys.argv[1], "reason": sys.argv[2]}))' "$OWNER_ID" "$REASON")
curl -sS -m 15 -X POST "http://127.0.0.1:8000/v1/admin/developer-ids/$DEV_ID/bind" \
  -H @"$HDR" -H 'content-type: application/json' -d "$BODY"
echo
rm -f "$HDR"
```

Read the audit trail (read-only):

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
xe "select created_at, developer_id, action, actor_account_id, previous_status, coalesce(previous_owner_account_id, ''), new_owner_account_id, reason from developer_id_audit_events order by created_at desc limit 20"
```

## Done when

- Exchange HEAD is `8149a0daf7ea5a95d7d58728829601003d895e32`
- SettleBridge HEAD is `747ae6fc474cb57ba1744c01681cf332d353df93`
  (not the docs commit that adds this file)
- D.2 prints `AUDIT TABLE OK`
- D.5 prints `PLAN 48 SMOKE OK`
- `AGENT_MANAGEMENT_V2` is still true (do not print the env file)
- No admin bind ran (`developer_id_audit_events` is empty)
- `developer_id_owners`, accounts, keys, and cards were not written
