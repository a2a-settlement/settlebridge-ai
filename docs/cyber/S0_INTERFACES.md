# SettleBridge Cyber: S0 frozen interfaces

Coordinator-owned. Workers read this and code against it. A worker that finds a contract wrong **stops and reports**; only the coordinator changes S0 and then re-briefs affected workers. No worker edits a file outside its ownership list.

## Frozen code (coordinator-owned, do not edit)

- `backend/app/models/challenge.py` — challenge tables, enums, `TASK_TRANSITIONS`, `CLAIM_TRANSITIONS`, `TERMINAL_TASK_STATES`.
- `backend/app/models/spend.py` — `account_budgets`, `spend_reservations`, `execution_usage`, `RESERVATION_TRANSITIONS`, `UsageCause`.
- `backend/app/models/github.py` — `JobStatus.CANCELLED`, the `CHALLENGE_*` `JobKind` values, `GitHubJob.dedupe_key` with the partial unique index `uq_github_jobs_open_dedupe_key` (open = pending or running).
- `backend/migrations/versions/a7c3e9f1b2d4_challenge_engine_and_spend.py` — the only migration this milestone. No worker adds a migration.
- `backend/app/schemas/challenge.py` — runner protocol, structured claim, validation evidence, API and report schemas, `MyLeaseOut`, `CyberTaskDetailOut`.
- `backend/app/config.py` — `CHALLENGE_*` and `SPEND_DEFAULT_*` settings.
- `challenge-runner/challenge_runner/protocol.py` and `docs/cyber/runner-protocol.schema.json` (generated from the Pydantic models; a test enforces it).
- `marketplace-next/lib/cyberContracts.ts` — UI props and API types.
- Stub signatures in worker-owned files (below). Workers fill bodies and may add private helpers, never change a public signature.

## Stubs and owners

- `services/github_jobs.py` (S1-min): `enqueue_job(db, *, kind, payload, dedupe_key=None, run_at=None) -> GitHubJob | None`, `cancel_jobs(db, *, dedupe_key_prefix) -> int`.
- `services/spend.py` (S2): `reserve_spend`, `record_usage`, `reconcile_reservation`, `release_reservation`, `expire_reservations`, `budget_status`; errors `BudgetExceeded`, `BudgetPaused`, `BudgetMissing`.
- `services/github_scoped_token.py` (S3): `scoped_read_token(installation_id, repo_full_name) -> str`.
- `challenge_runner/fetcher` (S3): `fetch_source(...) -> FetchOutput`.
- `challenge_runner/sandbox` (P1-2): `run_job(job, *, artifact_root) -> RunnerResult`; `SandboxUnavailable`.
- `challenge_runner/observers` (P1-V): `Observer` protocol, `get_observer(name)`.
- `challenge_runner/challenger` (P1-4): `run_challenger(bundle, *, out_dir, model_client, max_claims=5)`.
- `services/challenge_orchestrator.py` (P1-3), `services/challenge_validity.py` (P1-V), `services/challenge_report.py` (P1-5).

## Dedupe keys

- Challenge jobs: `chal:{task_id}:{stage}:{contract_version}` where stage is `fetch|deps|baseline|attempt:{attempt_id}|validate:{claim_id}|report`.
- Spend reservations: `spend:{subject}:{contract_version}`; subject is `challenge_attempt:{id}` or `challenge_validate:{claim_id}`.
- Execution ids: `exec:{task_id}:{purpose}:{n}` (purpose: `deps|baseline-base|baseline-target|attempt|positive|negative|reference|rederive|rerun-1..3|minimized`). A platform retry of the same execution gets a new id and cause `platform_retry`, `charged=false`.
- Findings: `chal:{repo}:{failure_class}:{claim_hash}`.

## Verdict rule (plan section 4)

`confirmed` only when: citation valid against the frozen contract's oracle sources; tamper audit clean; verifier observer fired on target; positive control succeeded and negative control stayed silent; differential consistent with the failure class; independent re-derivation fired; 3 of 3 reruns and the minimized reproducer fired. Control failure or flaky result → `inconclusive` with the reason. No citation → `invalid_or_duplicate` before anything runs. No observer for the class → adjudication. Never print "secure"; every verdict carries "Under the stated property, environment, and budget."

## Role and conflict rule

Challenger, verifier, and adjudicator operators are each different from the author and the solver, and from each other. Platform workers use operator ids `platform:challenger`, `platform:verifier`. The challenger sees only the frozen bundle (contract, source tarball, diff).

## UI status vocabulary (P0-F7 implements, everyone imports)

Icons from `lucide-react` only, `w-4 h-4` (`w-3.5 h-3.5` for `sm`), always next to a text label, icon `aria-hidden`.

- `done`: `CheckCircle2`, `text-money-dark`. Only for a step backed by a recorded fact.
- `in_progress`: `CircleDot`, `text-navy-700`.
- `not_started`: `Circle`, `text-gray-400`.
- `blocked`: `Ban`, `text-danger`; reason shown.
- `inconclusive`: `CircleHelp`, `text-warning-dark`; reason shown.
- `not_assessed`: `CircleDashed`, `text-gray-500`, dashed border, label "Not assessed".
- `needs_decision`: `AlertCircle`, `text-warning-dark`.

Repo status (never green): `needs_attention` (AlertCircle), `task_in_progress` (CircleDot), `no_open_tasks` (Circle, gray), `paused` (PauseCircle), `setup_needed` (Plug), `not_assessed` (CircleDashed). Repo icon: `FolderGit2`. Attention sorts first.

Funding: `balance_checked` Scale "Balance checked — not reserved"; `funds_held` Lock "Reserved — held for active claim"; `payment_pending` Clock "Payment pending"; `payment_review` Eye "Payment under review — funds held"; `paid` CheckCircle2 "Paid" (only green funding state); `not_paid` Undo2 "Not paid — refunded or cancelled".

Role switch: `Wallet` + "Funder / Maintainer" (`fund`), `Wrench` + "Solver / Challenger" (`solve`). Labels visible on mobile. `aria-pressed`. Active segment uses the money accent.

Caption wherever checks or verdicts appear: `CHECKS_CAPTION` from `lib/cyberContracts.ts`.

Workers confirm every icon name exists in the installed `lucide-react` (`node_modules/lucide-react/dist/lucide-react.d.ts`) before use; if one is missing, use the existing alias and report it.

## Task progress mapping (P0-F6 implements `lib/taskProgress.ts`)

`buildTaskProgress(input: TaskProgressInput, view: WorkView): TaskProgressModel`. Pure; may import from `@/` only with `import type`.

Detail steps: Claimed, Patch submitted, Verification, Review, Payment.
- `available`: all `not_started`.
- `claimed`: Claimed `in_progress`.
- `pr_submitted`: Claimed `done`; Patch submitted `in_progress`.
- `evaluating`: Claimed, Patch submitted `done`; Verification `in_progress` ("Checks are running on {short_sha}").
- `needs_maintainer_review`, `awaiting_merge`: Verification `done` only if `checks.state == "passed"` (detail "Required checks passed on {short_sha}"), else `blocked` (failed) or `in_progress` (pending); Review `in_progress` (`needs_decision` when `open_findings > 0`).
- `settling`: Review `done`; Payment `in_progress`.
- `completed` with `paid`: all `done` ("Paid {n} ATE (test)").
- `cancelled` or `not_paid`: the furthest step reached is `blocked` with reason "Cancelled" or "Not paid — refunded or cancelled"; later steps `not_started`.
- Before M2 (`challenge_verdict == null`), Verification's detail adds "Independent challenge: Not assessed".

Flow steps (cards and commercial wording): Alert (`done` always), Agent repair (Claimed + Patch submitted; `done` when both done), Independent challenge (`not_assessed` until a verdict; `done` for `no_failure_found` is **not** allowed — use `not_started` styling with "No failure found under the stated property, environment, and budget"; `confirmed` → `needs_decision` until the finding is decided; `inconclusive` → `inconclusive`), Acceptance (Review), Payment.

Next action copy:
- fund + `needs_maintainer_review`: "Awaiting maintainer review" / "Required checks passed. The policy assessment needs your review." / "Review pull request →" (pr_url). With `open_findings > 0`: sentence "One challenger finding needs a decision." (pluralize) / "Review finding →".
- solve + same: "Waiting for maintainer review" / "Nothing is needed from you."
- solve + `claimed`: "Open a pull request" / "Open a pull request before {work_expires_at}." / "View task terms →" (`/bounties/{id}`).
- `evaluating`: "Checks running" / "Checks are running on {short_sha}. Nothing is needed now."
- funding `payment_review`: "Payment under review" / "Payment under review — funds held. GitHub has not confirmed the alert is fixed."
- `completed`: "Paid" / "Paid {n} ATE (test)." / "View ledger entry →" (`/dashboard/wallet`).
- Every other combination has defined copy; tests cover every `work_state` × `funding_state` × view.

## Read-only endpoints added this milestone

- `GET /api/v1/github/leases/mine` → `MyLeasesOut` (P0-F1).
- `GET /api/v1/cyber/tasks/{bounty_id}` → `CyberTaskDetailOut` (P0-F6). Unknown or non-public → 404.
- `GET /api/v1/challenges/{id}` → `ChallengeDetailOut` (P1-5). Other tenants → 404.
- `POST /api/v1/challenges` (operator only, `Idempotency-Key` required), `GET /api/v1/challenges/{id}/report` → JWS text (P1-5).

## Tests and commands

- Backend: `/home/rich/miniconda3/envs/settlebridge_ai/bin/python -m pytest -q -p no:cacheprovider <your test files>` from `backend/`.
- Runner: same interpreter, from `challenge-runner/`.
- Frontend: `npx --no-install tsc --noEmit -p .`, `npx --no-install eslint <your files>`, `npm run -s test:unit` from `marketplace-next/`. Never `next build` in a worker.
- No network in tests, no live APIs, no databases. Mock `httpx` and subprocess calls.

## Wave 2 addendum (coordinator)

- `components/RepoCard.tsx` (frozen): default `RepoCard(RepoCardProps)` and `sortRepoCards(cards)`; attention sorts first. P0-F3 and P0-F4 import it and never edit it.
- `components/marketplace-pages/MyClaims.tsx` (frozen): the existing claims list moved out of `Dashboard.tsx`.
- `components/marketplace-pages/SolverHome.tsx` (P0-F4 owns): default export, no props. `Dashboard.tsx` (P0-F3 owns) renders it for the `solve` view.
- Spend: charged usage is capped at `reserved_cents`; `reservation_exhausted(reservation)` is true once the hold is used, and the orchestrator must stop that attempt.
- Lease claim replays rotate the lease token; a client holding a stale token replays the claim with the same `Idempotency-Key` to get a fresh one.
