# Droplet plan 49: bind 14 owner-approved Developer IDs

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-16.

**Depends on plan 48 (finished, smoke OK):**

- Exchange at `8149a0daf7ea5a95d7d58728829601003d895e32`
- SettleBridge at `747ae6fc474cb57ba1744c01681cf332d353df93`
- `developer_id_audit_events` exists and is empty.

**This plan:**

- Calls the operator-only override `POST /v1/admin/developer-ids/{developer_id}/bind`
  once for each of the 14 IDs below. Each call binds the ID to the owner
  account and writes exactly one audit row.
- Authenticates as the exchange operator account
  `c7d9cc7d-9117-444d-b0d8-d1b677f53015` (`a2a-settlement-mediator`). Its key
  is read from `/home/clawdbot/a2a-settlement-mediator/.env`
  (`A2A_OPERATOR_API_KEY`), checked against that account's bcrypt hash, and
  written only to a mode-600 header file that B.2 deletes. The key is never
  printed or passed on a command line.
- Binds to owner `07e2662c-617d-4771-a34c-873da0605c69`
  (`TruthSetter-Requester`, developer_id `truthsetter-clawd`), which the user
  confirmed.

**This plan does not:**

- Bind `mcp`, `truthsetter-clawd`, any test-cohort ID, or any ID not listed
  below.
- Create, change, suspend, or delete accounts, keys, Agent Cards, or `is_test`.
- Change code, restart services, or edit `.env`.

## IDs and the approved snapshot

The user approved each bind from this snapshot, taken on 2026-11-16. Before
each bind, the plan re-reads the ID. If status, owner, account count, or the
set of contact emails differs from the snapshot, it stops without binding
that ID. Any change since approval means the user must look again.

| Developer ID | Accounts | Contact emails (sorted, distinct) |
|---|---|---|
| rich-truthsetter-3 | 1 | rich3@truthsetter.com |
| truthsetter | 5 | rich@truthsetter.com |
| truthsetter-svc | 1 | richard.s.smith@gmail.com |
| rich-truthsetter | 1 | rich@truthsetter.com |
| rich-truthsetter-2 | 1 | rich2@truthsetter.com |
| rich-smith-svc | 1 | richard.s.smith@gmail.com |
| alphasignal-fund | 6 | admin@alphasignal.fund |
| eaftos | 1 | ops@eaftos.com |
| openclaw-eaftos | 2 | rich@truthsetter.com,richard.s.smith@gmail.com |
| sentinelrecon-v1 | 1 | rich@truthsetter.com |
| sentinelrecon-20260905012235 | 1 | richard.s.smith@gmail.com |
| settlebridge | 1 | gateway@settlebridge.ai |
| settlebridge-pilot | 1 | pilot-solver@test.settlebridge.ai |
| widrss | 7 | ops@example.com,widrss@users.noreply.github.com |

All 14 must be `legacy_open` with no owner. `rich-truthsetter-3` goes first
as a canary in B.1.

Effects after binding (expected, approved):

- These IDs show under the owner's My agents in the marketplace.
- A new registration under any of these IDs now needs the owner's key, or
  the key of an agent the owner controls. For example,
  `sentinelrecon/scripts/register.py` (developer_id `sentinelrecon-v1`) must
  pass an owner key. Existing agents and their keys are unchanged.
- `settlebridge` holds the platform gateway account. The owner can now
  rotate its key or suspend it from the marketplace.

## Rules (same as plans 1–48)

- Run blocks as `root`, one block at a time. Wait for each block to finish
  and check its result before starting the next. Each block uses
  `set -euo pipefail` and stops on the first failure. If any command or
  `test` fails, **stop and report** the failing line and its output. Do not
  work around it.
- Never print `.env` values, tokens, or keys. The operator key exists only in
  `/root/plan49-auth.hdr` (mode 600) between B.0 and the end of B.2. If any
  block fails, run `rm -f /root/plan49-auth.hdr` before reporting.
- One SSH session. Do not open a burst of new SSH connections.
- Database reads use `psql -q -At`. The only writes are the 14 bind API
  calls. The plan runs no SQL writes, except P.2 (rollback only).
- P.2 is rollback only. Do not run it unless the user asks.

## P.1 Pre-flight (read-only)

```bash
set -euo pipefail
PLAN=/root/deploy-plans/2026-11-16-developer-id-binds/DEVELOPER_ID_BINDS_PLAN.md
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
OWNER=07e2662c-617d-4771-a34c-873da0605c69
OPERATOR=c7d9cc7d-9117-444d-b0d8-d1b677f53015

test "$(git -C /home/clawdbot/a2a-settlement rev-parse HEAD)" = "8149a0daf7ea5a95d7d58728829601003d895e32"
systemctl is-active --quiet a2a-exchange
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  http://127.0.0.1:8000/v1/admin/developer-ids/plan49-preflight/bind \
  -H 'content-type: application/json' -d '{"owner_account_id":"x","reason":"x"}' || true)
test "$code" = 401

test "$(xe "select status || '|' || developer_id from accounts where id = '$OWNER'")" = "active|truthsetter-clawd"
test "$(xe "select status || '|' || bot_name from accounts where id = '$OPERATOR'")" = "operator|a2a-settlement-mediator"
test "$(xe "select count(*) from accounts where status = 'operator'")" = 1
test -f /home/clawdbot/a2a-settlement-mediator/.env
test "$(grep -cE '^\s*(export\s+)?A2A_OPERATOR_API_KEY=.+' /home/clawdbot/a2a-settlement-mediator/.env)" = 1
test "$(xe "select count(*) from developer_id_audit_events")" = 0
test ! -e /root/plan49-auth.hdr

echo "accounts: $(xe "select count(*) from accounts")"
echo "owner rows by status:"; xe "select status, count(*) from developer_id_owners group by status order by status"
echo "mcp: $(xe "select status || '|' || coalesce(owner_account_id, '') from developer_id_owners where developer_id = 'mcp'")"

cat > /root/plan49-expected.txt <<'EXP'
rich-truthsetter-3|1|rich3@truthsetter.com
truthsetter|5|rich@truthsetter.com
truthsetter-svc|1|richard.s.smith@gmail.com
rich-truthsetter|1|rich@truthsetter.com
rich-truthsetter-2|1|rich2@truthsetter.com
rich-smith-svc|1|richard.s.smith@gmail.com
alphasignal-fund|6|admin@alphasignal.fund
eaftos|1|ops@eaftos.com
openclaw-eaftos|2|rich@truthsetter.com,richard.s.smith@gmail.com
sentinelrecon-v1|1|rich@truthsetter.com
sentinelrecon-20260905012235|1|richard.s.smith@gmail.com
settlebridge|1|gateway@settlebridge.ai
settlebridge-pilot|1|pilot-solver@test.settlebridge.ai
widrss|7|ops@example.com,widrss@users.noreply.github.com
EXP
test "$(wc -l < /root/plan49-expected.txt)" = 14

MISMATCH=0
while IFS='|' read -r dev cnt emails; do
  fp=$(xe "select o.status || '|' || coalesce(o.owner_account_id, '') || '|' || count(a.id) || '|' || coalesce(string_agg(distinct a.contact_email, ',' order by a.contact_email), '') from developer_id_owners o left join accounts a on a.developer_id = o.developer_id where o.developer_id = '$dev' group by o.status, o.owner_account_id")
  if [ "$fp" = "legacy_open||$cnt|$emails" ]; then
    echo "match  $dev ($cnt)"
  else
    echo "HOLD   $dev expected 'legacy_open||$cnt|$emails' got '$fp'"
    MISMATCH=1
  fi
done < /root/plan49-expected.txt
test "$MISMATCH" = 0

if grep -qE '[<][A-Z_]+[>]' "$PLAN"; then
  echo "PLAN STILL HAS PLACEHOLDERS — stop"
  exit 1
fi
echo "P.1 OK"
```

Continue only when every line says `match` and the block prints `P.1 OK`.
Record the account count and owner-row counts. Expected: 103 accounts;
claimed 1, legacy_open 45; `mcp` is `legacy_open|`.

## B.0 Load the operator key (no output of the key)

Reads the key from the mediator `.env` and checks it with bcrypt against the
operator account's stored hash. Writes the auth header to a mode-600 file.
Prints only whether it matched.

```bash
set -euo pipefail
umask 077
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
OPERATOR=c7d9cc7d-9117-444d-b0d8-d1b677f53015

OP_HASH=$(xe "select api_key_hash from accounts where id = '$OPERATOR' and status = 'operator'")
test -n "$OP_HASH"
OP_HASH="$OP_HASH" /home/clawdbot/a2a-settlement/.venv/bin/python - /root/plan49-auth.hdr <<'PY'
import os
import sys

import bcrypt

key = None
with open("/home/clawdbot/a2a-settlement-mediator/.env") as fh:
    for line in fh:
        s = line.strip()
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if s.startswith("A2A_OPERATOR_API_KEY="):
            key = s.split("=", 1)[1].strip().strip('"').strip("'")
if not key:
    sys.exit("operator key not found in mediator .env")
if not bcrypt.checkpw(key.encode("utf-8"), os.environ["OP_HASH"].encode("utf-8")):
    sys.exit("mediator key does not match operator account c7d9cc7d; stop")
with open(sys.argv[1], "w") as out:
    out.write(f"Authorization: Bearer {key}\n")
print("operator key matches c7d9cc7d (key not printed)")
PY
unset OP_HASH
chmod 600 /root/plan49-auth.hdr
test "$(stat -c '%a %U' /root/plan49-auth.hdr)" = "600 root"
echo "B.0 OK"
```

If the key does not match, stop. Do not look for the key elsewhere. Report it,
and the user will supply the operator key another way.

## B.1 Canary bind: rich-truthsetter-3

Defines the bind function (also used by B.2), binds one ID, and checks that
exactly one audit row was written.

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
OWNER=07e2662c-617d-4771-a34c-873da0605c69
OPERATOR=c7d9cc7d-9117-444d-b0d8-d1b677f53015
HDR=/root/plan49-auth.hdr
test -f "$HDR"

cat > /root/plan49-bind.sh <<'FN'
bind_one() {
  local dev="$1" cnt="$2" emails="$3" reason="$4"
  local fp code tmp body
  fp=$(xe "select o.status || '|' || coalesce(o.owner_account_id, '') || '|' || count(a.id) || '|' || coalesce(string_agg(distinct a.contact_email, ',' order by a.contact_email), '') from developer_id_owners o left join accounts a on a.developer_id = o.developer_id where o.developer_id = '$dev' group by o.status, o.owner_account_id")
  if [ "$fp" = "claimed|$OWNER|$cnt|$emails" ] \
     && [ "$(xe "select count(*) from developer_id_audit_events where developer_id = '$dev' and new_owner_account_id = '$OWNER' and reason like 'plan49:%'")" = 1 ]; then
    echo "skip   $dev (already bound by plan 49)"
    return 0
  fi
  if [ "$fp" != "legacy_open||$cnt|$emails" ]; then
    echo "HOLD   $dev expected 'legacy_open||$cnt|$emails' got '$fp' — not bound"
    return 1
  fi
  body=$(python3 -c 'import json,sys; print(json.dumps({"owner_account_id": sys.argv[1], "reason": sys.argv[2]}))' "$OWNER" "$reason")
  tmp=$(mktemp)
  code=$(curl -s -o "$tmp" -w '%{http_code}' -m 15 -X POST \
    "http://127.0.0.1:8000/v1/admin/developer-ids/$dev/bind" \
    -H @"$HDR" -H 'content-type: application/json' -d "$body" || true)
  if [ "$code" != 200 ]; then
    echo "FAIL   $dev bind returned $code: $(head -c 300 "$tmp")"
    rm -f "$tmp"
    return 1
  fi
  python3 - "$tmp" "$dev" "$OWNER" <<'PY'
import json
import sys

r = json.load(open(sys.argv[1]))
dev, owner = sys.argv[2], sys.argv[3]
assert r["developer_id"] == dev, r
assert r["owner_account_id"] == owner, r
assert r["status"] == "claimed", r
assert r["previous_status"] == "legacy_open", r
assert r["previous_owner_account_id"] is None, r
print(f"bound  {dev} audit_event_id={r['audit_event_id']}")
PY
  rm -f "$tmp"
  test "$(xe "select count(*) from developer_id_audit_events where developer_id = '$dev'")" = 1
  test "$(xe "select actor_account_id || '|' || new_owner_account_id || '|' || coalesce(previous_status, '') from developer_id_audit_events where developer_id = '$dev'")" = "$OPERATOR|$OWNER|legacy_open"
}
FN
. /root/plan49-bind.sh

bind_one rich-truthsetter-3 1 "rich3@truthsetter.com" \
  "plan49: owner-approved bind 2026-11-16; rich-truthsetter-3 traced to owner identity rich3@truthsetter.com"
test "$(xe "select count(*) from developer_id_audit_events")" = 1
echo "B.1 OK"
```

Stop here if B.1 fails, and run `rm -f /root/plan49-auth.hdr`. Continue to
B.2 only after `B.1 OK`.

## B.2 Bind the other 13

Stops at the first failure. A rerun skips IDs that plan 49 already bound.
Always deletes the auth header file at the end.

```bash
set -euo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
OWNER=07e2662c-617d-4771-a34c-873da0605c69
OPERATOR=c7d9cc7d-9117-444d-b0d8-d1b677f53015
HDR=/root/plan49-auth.hdr
trap 'rm -f /root/plan49-auth.hdr' EXIT
test -f "$HDR"
. /root/plan49-bind.sh

R="plan49: owner-approved bind 2026-11-16;"
bind_one truthsetter 5 "rich@truthsetter.com" "$R truthsetter traced to owner identity rich@truthsetter.com"
bind_one truthsetter-svc 1 "richard.s.smith@gmail.com" "$R truthsetter-svc traced to owner identity richard.s.smith@gmail.com"
bind_one rich-truthsetter 1 "rich@truthsetter.com" "$R rich-truthsetter traced to owner identity rich@truthsetter.com"
bind_one rich-truthsetter-2 1 "rich2@truthsetter.com" "$R rich-truthsetter-2 traced to owner identity rich2@truthsetter.com"
bind_one rich-smith-svc 1 "richard.s.smith@gmail.com" "$R rich-smith-svc traced to owner identity richard.s.smith@gmail.com"
bind_one alphasignal-fund 6 "admin@alphasignal.fund" "$R alphasignal-fund traced to owner (admin@alphasignal.fund; principals shared with TruthSetter-Requester)"
bind_one eaftos 1 "ops@eaftos.com" "$R eaftos confirmed by owner (ops@eaftos.com)"
bind_one openclaw-eaftos 2 "rich@truthsetter.com,richard.s.smith@gmail.com" "$R openclaw-eaftos traced to owner identities rich@truthsetter.com and richard.s.smith@gmail.com"
bind_one sentinelrecon-v1 1 "rich@truthsetter.com" "$R sentinelrecon-v1 traced to owner identity rich@truthsetter.com"
bind_one sentinelrecon-20260905012235 1 "richard.s.smith@gmail.com" "$R sentinelrecon-20260905012235 traced to owner identity richard.s.smith@gmail.com"
bind_one settlebridge 1 "gateway@settlebridge.ai" "$R settlebridge platform gateway account, bound at owner's explicit request"
bind_one settlebridge-pilot 1 "pilot-solver@test.settlebridge.ai" "$R settlebridge-pilot traced to owner (registration principal shared with TruthSetter-Requester)"
bind_one widrss 7 "ops@example.com,widrss@users.noreply.github.com" "$R widrss owner's GitHub handle; owner confirmed both contact emails and all 7 accounts"

test "$(xe "select count(*) from developer_id_audit_events")" = 14
echo "B.2 OK"
```

The `trap` removes `/root/plan49-auth.hdr` whether the block succeeds or
fails.

## V.1 Verify (read-only)

```bash
set -uo pipefail
PG="docker exec a2a-settlement-postgres-1"
xe() { $PG psql -U a2a -d a2a_exchange -q -At -c "$1"; }
OWNER=07e2662c-617d-4771-a34c-873da0605c69
OPERATOR=c7d9cc7d-9117-444d-b0d8-d1b677f53015
FAIL=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }

[ ! -e /root/plan49-auth.hdr ] && ok "auth header file removed" || { rm -f /root/plan49-auth.hdr; bad "auth header file was still present (now removed)"; }

IDS="'rich-truthsetter-3','truthsetter','truthsetter-svc','rich-truthsetter','rich-truthsetter-2','rich-smith-svc','alphasignal-fund','eaftos','openclaw-eaftos','sentinelrecon-v1','sentinelrecon-20260905012235','settlebridge','settlebridge-pilot','widrss'"
n=$(xe "select count(*) from developer_id_owners where developer_id in ($IDS) and status = 'claimed' and owner_account_id = '$OWNER' and claimed_at is not null")
[ "$n" = 14 ] && ok "14 IDs claimed by owner" || bad "$n of 14 IDs claimed by owner"
n=$(xe "select count(*) from developer_id_audit_events where developer_id in ($IDS) and action = 'admin_bind' and actor_account_id = '$OPERATOR' and new_owner_account_id = '$OWNER' and previous_status = 'legacy_open' and reason like 'plan49:%'")
[ "$n" = 14 ] && ok "14 audit rows, one per ID" || bad "$n audit rows match"
n=$(xe "select count(*) from developer_id_audit_events")
[ "$n" = 14 ] && ok "no other audit rows" || bad "$n total audit rows"
n=$(xe "select count(*) from (select developer_id from developer_id_audit_events group by developer_id having count(*) > 1) t")
[ "$n" = 0 ] && ok "no ID has more than one audit row" || bad "$n IDs have duplicate audit rows"

n=$(xe "select count(*) from developer_id_owners where status = 'claimed'")
[ "$n" = 15 ] && ok "claimed rows 15 (truthsetter-clawd + 14)" || bad "claimed rows $n (expected 15)"
n=$(xe "select count(*) from developer_id_owners where status = 'legacy_open'")
[ "$n" = 31 ] && ok "legacy_open rows 31" || bad "legacy_open rows $n (expected 31)"
m=$(xe "select status || '|' || coalesce(owner_account_id, '') from developer_id_owners where developer_id = 'mcp'")
[ "$m" = "legacy_open|" ] && ok "mcp still unclaimed" || bad "mcp is '$m'"
n=$(xe "select count(*) from accounts")
[ "$n" = 103 ] && ok "accounts 103 (unchanged)" || bad "accounts $n (expected 103; report, do not change)"

echo "audit trail:"
xe "select created_at, developer_id, previous_status, new_owner_account_id from developer_id_audit_events order by created_at"

for u in http://127.0.0.1:8000/health http://127.0.0.1:8002/health https://market.settlebridge.ai/agents; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u" || true)
  [ "$code" = 200 ] && ok "$code $u" || bad "$code $u"
done
rm -f /root/plan49-bind.sh

if [ "$FAIL" -ne 0 ]; then
  echo "PLAN 49 VERIFY FAILED"
  exit 1
fi
echo "PLAN 49 VERIFY OK"
```

The accounts check assumes nobody registered a new account during the run.
If it reports a different number, report it. Do not delete anything.

## P.2 Rollback (only if the user asks)

There is no unbind API. This block returns plan 49's rows to `legacy_open`
with no owner, using a direct SQL update. It only touches rows that are still
claimed by the owner and have a `plan49:` audit row. It leaves the audit rows
in place as the record of the binds. The rollback itself is not written to
the audit table, so report that it ran.

```bash
set -euo pipefail
rm -f /root/plan49-auth.hdr
PG="docker exec a2a-settlement-postgres-1"
OWNER=07e2662c-617d-4771-a34c-873da0605c69
$PG psql -U a2a -d a2a_exchange -q -At -v ON_ERROR_STOP=1 -c "
update developer_id_owners o
   set status = 'legacy_open', owner_account_id = null, claimed_at = null
 where o.status = 'claimed'
   and o.owner_account_id = '$OWNER'
   and o.developer_id <> 'truthsetter-clawd'
   and exists (select 1 from developer_id_audit_events e
                where e.developer_id = o.developer_id
                  and e.reason like 'plan49:%'
                  and e.new_owner_account_id = '$OWNER')
returning o.developer_id"
echo "claimed rows now: $($PG psql -U a2a -d a2a_exchange -q -At -c "select count(*) from developer_id_owners where status = 'claimed'")"
echo "ROLLBACK DONE"
```

## Done when

- P.1 shows `match` for all 14 IDs and prints `P.1 OK`
- B.0 prints `operator key matches c7d9cc7d (key not printed)`
- B.1 prints `B.1 OK`, and B.2 prints `B.2 OK`
- V.1 prints `PLAN 49 VERIFY OK`
- `/root/plan49-auth.hdr` does not exist
- `mcp`, test-cohort IDs, accounts, keys, and cards are unchanged
