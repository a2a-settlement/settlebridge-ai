# Droplet plan 50: archive and remove three stale checkouts under /root

Target: `do-droplet` (`root@64.225.21.214`). Written 2026-11-17.

Independent of every other plan. Changes no code, restarts nothing, and reads
no `.env` file.

**Why.** Three old checkouts sit under `/root` next to the live ones in
`/home/clawdbot`. A read-only check on 2026-11-17 found that no systemd unit,
cron job, container, or running process refers to them. Two of them hold
uncommitted work that exists nowhere else, so this plan archives each one in
full before removing it.

| Checkout | HEAD | Last file change | Uncommitted |
|---|---|---|---|
| `/root/a2a-settlement` | `d38805a` | 2026-08-01 | 10 modified, 9 untracked. Includes the Tempo/x402 deposit-verification prototype (`exchange/tempo_verify.py`, `tests/test_deposit_verify.py`) and five docs from March. |
| `/root/settlebridge-ai` | `2ad5948` | 2026-09-05 | 4 modified, 4 untracked, 1 stash (April). |
| `/root/settlement-conformance` | `4408fbf` | 2026-08-27 | 1 modified (a link fix already on `main`). |

**This plan:**

- Writes one tarball per checkout to `/root/archive/stale-checkouts-<UTC>/`
  (mode 700). Each tarball holds the whole directory including `.git`, so the
  stash and every uncommitted file are kept. Only `.venv-demo`, `.venv`,
  `node_modules` and `__pycache__` are left out.
- Also writes, per checkout, the tracked changes as a binary patch and the
  `git status` listing, so the work can be read without unpacking.
- Verifies each tarball, then removes the three directories.

**This plan does not:**

- Touch anything under `/home/clawdbot`, `/opt`, or `/etc`.
- Remove any other directory under `/root`.
- Delete the archive. Deleting it later is a separate decision.

## Rules (same as plans 1–49)

- Run blocks as `root`, one block at a time. Wait for each block to finish
  and check its result before starting the next. Each block uses
  `set -euo pipefail` and stops on the first failure. If any command or
  `test` fails, **stop and report** the failing line and its output. Do not
  work around it.
- One SSH session. Do not open a burst of new SSH connections.
- Do not print file contents from the checkouts. `demo/server.log` and
  `demo/demo_exchange.db` may hold keys.
- S.4 is rollback only. Do not run it unless the user asks.

## S.1 Pre-flight (read-only)

The checkouts must still match the 2026-11-17 snapshot. Any difference means
someone has used them since, and the user must look again.

```bash
set -euo pipefail
check() { # dir head changed-count stash-count
  test -d "$1/.git"
  test "$(git -C "$1" rev-parse --short=7 HEAD)" = "$2"
  test "$(git -C "$1" status --porcelain | wc -l)" = "$3"
  test "$(git -C "$1" stash list | wc -l)" = "$4"
  test "$(git -C "$1" log --oneline '@{u}..HEAD' | wc -l)" = 0
  echo "ok $1"
}
check /root/a2a-settlement        d38805a 19 0
check /root/settlebridge-ai       2ad5948 8  1
check /root/settlement-conformance 4408fbf 1  0

PAT='/root/(a2a-settlement|settlebridge-ai|settlement-conformance)(/|"|$| )'
! grep -rIlE "$PAT" /etc/systemd/system /etc/caddy /etc/cron.d /var/spool/cron 2>/dev/null
for p in /proc/[0-9]*; do
  c=$(readlink "$p/cwd" 2>/dev/null || true)
  case "$c" in /root/a2a-settlement*|/root/settlebridge-ai*|/root/settlement-conformance*)
    echo "process $(basename "$p") is running in $c"; exit 1;; esac
done
! docker ps -q | xargs -r docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}} {{range .Mounts}}{{.Source}} {{end}}' | grep -E "$PAT"

test "$(df --output=avail -BM /root | tail -1 | tr -dc 0-9)" -gt 1024
for u in a2a-exchange a2a-mediator a2a-mcp mcp-trust-gateway settlebridge settlebridge-marketplace caddy; do systemctl is-active --quiet $u; done
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
echo "PRE-FLIGHT OK"
```

## S.2 Archive and verify

```bash
set -euo pipefail
TS=$(date -u +%Y%m%dT%H%M%SZ); A=/root/archive/stale-checkouts-$TS
install -d -m 700 /root/archive "$A"
echo "$A" > /root/archive/stale-checkouts-current
for name in a2a-settlement settlebridge-ai settlement-conformance; do
  d=/root/$name
  git -C "$d" status --porcelain > "$A/$name.status.txt"
  git -C "$d" rev-parse HEAD > "$A/$name.head.txt"
  git -C "$d" diff --binary HEAD > "$A/$name.tracked.patch"
  test -s "$A/$name.tracked.patch"
  tar -C /root -czf "$A/$name.tar.gz" \
    --exclude='.venv-demo' --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' \
    "$name"
  tar -tzf "$A/$name.tar.gz" > "$A/$name.files.txt"
  grep -qx "$name/.git/HEAD" "$A/$name.files.txt"
done
git -C /root/settlebridge-ai stash show -p --binary 'stash@{0}' > "$A/settlebridge-ai.stash0.patch"
test -s "$A/settlebridge-ai.stash0.patch"

# Every uncommitted file must be in its tarball.
for name in a2a-settlement settlebridge-ai settlement-conformance; do
  git -C /root/$name ls-files --modified --others --exclude-standard \
    | grep -vE '(^|/)(\.venv-demo|\.venv|node_modules|__pycache__)/' \
    | while read -r f; do
        grep -qxF "$name/$f" "$A/$name.files.txt" || { echo "MISSING from archive: $name/$f"; exit 1; }
      done
done
for f in exchange/tempo_verify.py tests/test_deposit_verify.py docs/federation.md; do
  grep -qxF "a2a-settlement/$f" "$A/a2a-settlement.files.txt"
done
grep -q '^settlebridge-ai/.git/refs/stash$' "$A/settlebridge-ai.files.txt"
(cd "$A" && sha256sum *.tar.gz > SHA256SUMS && gzip -t *.tar.gz)
chmod -R go-rwx "$A"
ls -l "$A"
echo "ARCHIVE OK ($A)"
```

## S.3 Remove the checkouts and check the services

Run only after S.2 printed `ARCHIVE OK`.

```bash
set -euo pipefail
A=$(cat /root/archive/stale-checkouts-current)
(cd "$A" && sha256sum -c --quiet SHA256SUMS)
BEFORE=$(df --output=avail -BM /root | tail -1 | tr -dc 0-9)
for name in a2a-settlement settlebridge-ai settlement-conformance; do
  test -s "$A/$name.tar.gz"
  test -d "/root/$name/.git"
  rm -rf --one-file-system "/root/$name"
  test ! -e "/root/$name"
done
AFTER=$(df --output=avail -BM /root | tail -1 | tr -dc 0-9)
echo "freed about $((AFTER - BEFORE)) MB"

for d in /home/clawdbot/a2a-settlement /home/clawdbot/settlebridge-ai; do test -d "$d/.git"; done
for u in a2a-exchange a2a-mediator a2a-mcp mcp-trust-gateway settlebridge settlebridge-marketplace caddy; do systemctl is-active --quiet $u; done
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8000/health | grep -qx 200
curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8002/health | grep -qx 200
echo "REMOVED OK; archive kept at $A"
```

## S.4 Rollback (only if the user asks)

Restores the three directories from the archive. The left-out directories
(`.venv-demo`, `.venv`, `node_modules`, `__pycache__`) are not restored.

```bash
set -euo pipefail
A=$(cat /root/archive/stale-checkouts-current)
(cd "$A" && sha256sum -c --quiet SHA256SUMS)
for name in a2a-settlement settlebridge-ai settlement-conformance; do
  test ! -e "/root/$name"
  tar -C /root -xzf "$A/$name.tar.gz"
  test "$(git -C /root/$name rev-parse HEAD)" = "$(cat "$A/$name.head.txt")"
done
echo "RESTORED"
```

## Done when

- S.3 printed `REMOVED OK`.
- `/root/a2a-settlement`, `/root/settlebridge-ai` and
  `/root/settlement-conformance` no longer exist.
- The archive directory holds three tarballs, three tracked patches, the
  stash patch and `SHA256SUMS`.
- Report the archive path and the space freed.

## Follow-up (not part of this plan)

The deposit-verification prototype and the five March docs are worth a
decision: bring them into `a2a-settlement` as a branch, or leave them in the
archive. To read them without unpacking, see `a2a-settlement.tracked.patch`
and `a2a-settlement.files.txt` in the archive directory.

## Run record

Run 2026-11-18 (UTC), S.1 to S.3, no changes to the plan.

- Archive: `/root/archive/stale-checkouts-20261118T004352Z`.
- Before S.3, each tarball was unpacked to a temporary directory and compared
  with its original using `diff -r`. All three were identical, and the
  settlebridge-ai stash was present in the unpacked copy.
- About 241 MB freed. All services active; exchange and SettleBridge health 200.
- S.4 was not run.
