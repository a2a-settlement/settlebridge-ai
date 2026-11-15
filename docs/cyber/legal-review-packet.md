# Legal and compliance review packet

**Product:** SettleBridge fix verifier, public repository `settlebridge-ai`  
**Packet date:** 15 November 2026  
**Supported release line named in `SECURITY.md`:** 1.4.0 and later versions published from the main branch  
**Status:** Engineering packet for counsel. This document is not a legal opinion. Sign-off lines at the end are blank until counsel completes them.

This packet is the review copy for three launch items:

1. Proof-of-fix receipt wording, scope, SLSA verification-summary results, VEX status mapping, and solver independence disclosure.
2. Coordinated vulnerability disclosure and embargo policy, as published in `SECURITY.md` and `docs/cyber/disclosure-policy.md`.
3. Model-provider terms and storage of exploit or reproducer artifacts for xAI, the Anthropic Cyber Verification Program, and Daybreak.

Strings in fenced blocks are copied from this repository, or are the launch receipt sentences this review is asked to approve. Checklists are the sign-off instrument. If a quoted source file changes after this packet, the review of that item restarts from the new text.

The packet quotes public product text only. It does not include model prompts or unpublished evaluation material.

## What this repository ships today

Counsel is reviewing wording for a receipt this service is specified to issue, and policy text that is already in the tree.

The public receipt route is `GET /api/v1/fix-verifications/{verification_id}/receipt`. Its response model is `ReceiptResponse` with one field, `receipt_id`. The route returns `{"receipt_id": "<id>"}` or 404. `request_verification_receipt` in `backend/app/services/fix_verifier.py` returns an id already stored on the row. It does not call the mediator, does not write an id, and does not return a signed document.

There is no `receipt_client.py` in this repository. The mediator client that does exist is `verify_patch` in `backend/app/services/mediator.py`. It POSTs canonical JSON to `{MEDIATOR_URL}/api/v1/verify-patch`. The shared secret is `MEDIATOR_WEBHOOK_SECRET`. The HMAC is SHA-256 over `f"{timestamp}."` plus those body bytes. Request headers are `X-A2ASE-Timestamp` and `X-A2ASE-Signature: sha256=<hex>`. There is no setting named `MEDIATOR_HMAC_SECRET`.

`Settings` in `backend/app/config.py` has no `environment` field. Production localhost checks in the launch preflight treat `--target-public-launch` as the production case, and also honor an `environment` or `ENVIRONMENT` value of `production` when a caller supplies one.

Launch flags, all defaulting off:

| Setting | Default in `Settings` |
| --- | --- |
| `FIX_VERIFIER_ENABLED` | `false` |
| `FIX_VERIFIER_PUBLIC_API` | `false` |
| `FIX_VERIFIER_PAYOUT_GATE` | `false` |
| `FIX_WARRANTY_ENABLED` | `false` |
| `FIX_WARRANTY_DAYS` | `90` |
| `FIX_WARRANTY_HOLDBACK_PCT` | `20` |
| `FIX_WARRANTY_REVIEW_SLA_DAYS` | `7` |
| `FIX_VERIFIER_DAILY_QUOTA` | `20` |
| `FIX_VERIFIER_RATE_LIMIT_PER_HOUR` | `5` |
| `FIX_VERIFIER_DAILY_COST_CAP_CENTS` | `2500` |
| `FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION` | `500` |
| `FIX_VERIFIER_OIDC_ENABLED` | `false` |
| `FIX_VERIFIER_OIDC_AUDIENCE` | `settlebridge` |
| `CHALLENGE_REPORT_KMS_KEY_ARN` | empty |
| `CHALLENGE_REPORT_KMS_REGION` | empty |
| `CHALLENGE_REPORT_SIGNING_KEY` | empty |
| `CHALLENGE_REPORT_KEY_ID` | `challenge-report-1` |
| `MEDIATOR_URL` | `https://mediator.a2a-settlement.org` |
| `MEDIATOR_WEBHOOK_SECRET` | empty |

The public router is mounted at `/api/v1/fix-verifications`. Every route answers 404 while `FIX_VERIFIER_PUBLIC_API` is false. Create and replay also require `FIX_VERIFIER_ENABLED`. When `FIX_VERIFIER_OIDC_ENABLED` and `FIX_VERIFIER_PUBLIC_API` are both true, a GitHub Actions OIDC bearer from `https://token.actions.githubusercontent.com` is accepted on create, read, and receipt. The token `aud` must match `FIX_VERIFIER_OIDC_AUDIENCE`.

Operators run this on the host before changing those flags:

```bash
python -m app.services.fix_verifier_preflight --target-public-launch
python -m app.services.fix_verifier_preflight --json --target-public-launch
```

`--target-public-launch` checks the current settings as if the four flags above were true, including the production localhost rule. It does not edit `.env`.

## Item A — Proof-of-fix receipt wording

### A.1 Verdict vocabulary

The fix-verifier verdict stored on `fix_verifications.verdict` is one of:

- `verified`
- `incomplete_fix_confirmed`
- `inconclusive`

`inconclusive` carries a machine-readable reason. Reasons this launch uses:

- `no_positive_control`
- `deterministic_violation`
- `controls_failed`
- `unsupported_project`
- `budget_exhausted`
- `infra_error`
- `failure_class_not_exercised`
- `tamper_detected`

Shipped fallback text in this repository, from `backend/app/services/verifier_client.py` and `backend/app/services/fix_verifier.py`:

```text
launch scope is pure-Python PyPI only
FIX_VERIFIER_ENABLED is false
```

`docs/cyber/disclosure-policy.md` states the same scope as: "Launch scope is pure-Python projects published on PyPI."

A `verified` verdict requires a positive control that fired on the pre-fix commit. A missing positive control is `inconclusive` / `no_positive_control`. It is never `verified`.

### A.2 Receipt sentence under review

The proof-of-fix receipt sentence for a `verified` verdict is:

```text
No failure found under the frozen contract.
```

That sentence is used for `verified` only. `incomplete_fix_confirmed` means a failure of the fix was found. `inconclusive` means the run did not reach `verified`. Those two verdicts do not carry the no-failure sentence.

The sentence does not say the software is secure, proven secure, free of defects, or fit for a purpose.

### A.3 Scope sentence already present on challenge reports

The challenge engine signs a different document: a compact JWS (`alg` `ES256`, `typ` `challenge-report+jws`) of `ChallengeReport`. That report's `scope_note` default, in `backend/app/schemas/challenge.py`, is:

```text
Under the stated property, environment, and budget.
```

The same sentence is the default `scope_note` on `ChallengeDetailOut`. The model module states: "A verdict always applies to the stated property, environment, and budget of its frozen contract."

`verdict_statement` in `backend/app/services/challenge_report.py` returns this exact string for verdict `no_failure_found` (no period at the end):

```text
No failure found under the stated property, environment, and budget
```

`backend/app/services/challenge_orchestrator.py` stores this form, with a period:

```text
No failure found under the stated property, environment, and budget.
```

`docs/cyber/S0_INTERFACES.md` states: Never print "secure"; every verdict carries "Under the stated property, environment, and budget."

These challenge-report sentences and the proof-of-fix sentence in A.2 are different strings. Counsel approves each one for the document it belongs to.

Challenge-report signing uses `CHALLENGE_REPORT_KMS_KEY_ARN` when that ARN is set (`ECC_NIST_P256`, `ECDSA_SHA_256`). The config comment states: "Used only when CHALLENGE_REPORT_KMS_KEY_ARN is unset. The private key stays in KMS." An ARN without `CHALLENGE_REPORT_KMS_REGION` fails closed. The PEM in `CHALLENGE_REPORT_SIGNING_KEY` is the fallback when the ARN is unset. The report docstring states: "The report never contains tokens, raw logs, or another tenant's data."

### A.4 Non-warranty scope, and the separate payout holdback

The receipt is a record of a verdict under a frozen contract. The holdback below is a payout term on a GitHub bounty. The holdback is configured by `FIX_WARRANTY_ENABLED`, `FIX_WARRANTY_DAYS`, `FIX_WARRANTY_HOLDBACK_PCT`, and `FIX_WARRANTY_REVIEW_SLA_DAYS`.

Config comment on those settings:

```text
Hold back part of a verified GitHub payout; forfeit only on a security-motivated
revert or an overlapping follow-up CVE inside the window.
```

`split_payout` holds back `ceil(amount * percent / 100)`. The payee's immediate share is never larger than `amount - ceil(amount * percent / 100)`. When the holdback percent is 20 and the split divides evenly, the immediate release percent is 80.

The holdback runs only when `FIX_VERIFIER_PAYOUT_GATE` and `FIX_WARRANTY_ENABLED` are both true and the holdback percent is greater than 0. The payout gate pays only a row whose verdict is `verified` and whose `receipt_id` is non-empty.

Shipped payout strings in `backend/app/services/github_settlement.py`:

```text
Security-motivated revert or overlapping follow-up advisory forfeits this holdback.
```

```text
The merged fix was verified as incomplete, so this bounty will not be paid. The escrow stays locked pending review.
```

```text
incomplete fix confirmed; payout refused
fix verification receipt missing; payout held
fix verification not verified; payout held
fix verification missing; payout held
```

Holdback outcomes in `backend/app/services/warranty_watcher.py`:

- A revert classified `true` forfeits the holdback (`trigger_kind` `security_revert`).
- An overlapping follow-up advisory forfeits the holdback (`trigger_kind` `followup_cve`). Overlap shares a file. When both sides name functions, they must share a function too.
- A revert classified `false` releases the holdback (`trigger_kind` `non_security_revert`).
- A revert classified `unknown` moves the warranty to `needs_review` and leaves the escrow where it is. `review_due_at` is `FIX_WARRANTY_REVIEW_SLA_DAYS` after that moment (default 7). The operator decision is `release` or forfeit, and the caller must be in `PLATFORM_ADMIN_USER_IDS`.
- A window that ends with no trigger releases the holdback (`trigger_kind` `window_elapsed`).
- A source that raises leaves the warranty `active`.

Warranty states are `active`, `needs_review`, `released`, and `forfeited`. State only moves forward.

Draft sentence for counsel to approve, reject, or replace. It is not a constant in this repository:

```text
No failure found under the frozen contract. This is not a warranty. The result applies only under the stated property, environment, and budget.
```

### A.5 SLSA verification summary

Alongside each proof-of-fix receipt, the launch issues a SLSA Verification Summary Attestation, predicate type:

```text
https://slsa.dev/verification_summary/v1
```

The statement type is in-toto Statement v1 (`https://in-toto.io/Statement/v1`), in a DSSE envelope, signed ES256 by AWS KMS (`ECC_NIST_P256`, `ECDSA_SHA_256`). The host holds the key ARN and `kid`. Private key material is not placed on the host.

Mapping from fix verdict to `verificationResult`:

| Fix verdict | `verificationResult` |
| --- | --- |
| `verified` | `PASSED` |
| `incomplete_fix_confirmed` | `FAILED` |
| `inconclusive` | `FAILED` |

`PASSED` and `FAILED` are the only result strings. `FAILED` on an `inconclusive` verdict means this policy did not reach `verified`. It does not mean a vulnerability was confirmed.

`resourceUri` and the subject digest name the repository and the fix commit. `policy.uri` is the proof-of-fix predicate type in A.6. Both the proof-of-fix statement and the verification summary carry the same RFC 3161 timestamp token and the same Merkle inclusion proof. A public launch uses a commercial timestamp authority. A free timestamp authority is a development stand-in.

Illustrative verification summary. Placeholder tokens stand for one repository, one commit, and one time. This body is not yet returned by `GET /api/v1/fix-verifications/{id}/receipt`.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "git+https://github.com/OWNER/REPO",
      "digest": {"gitCommit": "FIX_COMMIT"}
    }
  ],
  "predicateType": "https://slsa.dev/verification_summary/v1",
  "predicate": {
    "timeVerified": "TIMESTAMP",
    "resourceUri": "git+https://github.com/OWNER/REPO",
    "policy": {
      "uri": "https://settlebridge.ai/attestations/proof-of-fix/v1"
    },
    "verificationResult": "PASSED"
  }
}
```

For any verdict other than `verified`, `verificationResult` in that predicate is `FAILED`.

### A.6 Proof-of-fix predicate and VEX mapping

Predicate type:

```text
https://settlebridge.ai/attestations/proof-of-fix/v1
```

Subject:

```json
[
  {
    "name": "git+https://github.com/OWNER/REPO",
    "digest": {"gitCommit": "FIX_COMMIT"}
  }
]
```

Predicate fields:

- `receipt_id`
- `vulnerability` with `id`, `aliases`, `alert_ref`
- `repo`, `base_commit`, `fix_commit`, `pr`
- `verdict`, `inconclusive_reason`
- `vex` with `status` and `justification`
- `failure_classes_tested`, `positive_control_on_base`
- `deterministic_findings_sha256`, `frozen_contract_sha256`, `challenge_report_sha256`
- `reproducibility` with `engine_sha`, `contractgen_version`, `sandbox_image_digest`, `prompt_version`, `model_ids`, `seeds`
- `verifier` with `id`, `operator`, `key_id`
- `independence` (A.7)
- `issued_at`, `nonce`

`reproducibility.prompt_version` is a version identifier. This packet does not include prompt text.

VEX `status` mapping. These three strings are the whole map:

| `verdict` | `vex.status` |
| --- | --- |
| `verified` | `fixed` |
| `incomplete_fix_confirmed` | `affected` |
| `inconclusive` | `under_investigation` |

The public client sets `vex_status` to `under_investigation` on `unsupported_project` and `infra_error`. This repository does not yet store a `vex.justification` string. Counsel either approves a justification phrase or records that `status` alone is the published VEX field for launch.

While a finding is embargoed, `docs/cyber/disclosure-policy.md` requires the receipt to state the verdict only. The receipt leaves out the reproducer, the affected code, and the steps that trigger the bug. A reproducer is published only after the finding is disclosed. See item B.

### A.7 Organizational independence

`solver_kind` is `house`, `third_party`, or `unknown`.

`independence_statement` in `backend/app/services/solver_identity.py` returns the following text. The house string is one sentence pair. The neutral string is used for `third_party`, `unknown`, and any other kind.

House (`solver_kind` `house`):

```text
The fix was produced by a SettleBridge-operated solver. Verifier and challenger are separate operator identities on the same platform; this receipt does not claim organizational independence.
```

Neutral (`third_party`, `unknown`, and any other kind):

```text
The solver principal is recorded on this receipt. This receipt does not state an independence conclusion.
```

A house solver is a platform-operated principal. Builtin platform operators `platform:verifier` and `platform:challenger` are house. `PLATFORM_HOUSE_PRINCIPALS` is the comma-separated list of additional canonical principals. A caller can lower a solver-kind claim. A caller cannot raise one. Platform admins and accounts with `account_kind` `house` are stored as `house`.

Receipt field `independence`:

```json
{
  "solver_kind": "house",
  "solver_principal_sha256": "HEX_SHA256",
  "same_platform_as_verifier": true,
  "statement": "The fix was produced by a SettleBridge-operated solver. Verifier and challenger are separate operator identities on the same platform; this receipt does not claim organizational independence."
}
```

`solver_principal_sha256` is the SHA-256 hex digest of the canonical principal `kind:value`. There is no `sha256:` prefix. A missing principal has no digest.

`verifier_independent_of_solver: true` is included only when `solver_kind` is `third_party`, the solver is not on the same platform as the verifier, and the lineage checks passed. Otherwise the field is omitted. It is not set to false. A house receipt omits it and carries the house statement above. A house receipt does not claim organizational independence.

The neutral statement is also what `independence_statement` returns for `third_party`. Counsel decides whether a receipt that includes `verifier_independent_of_solver: true` keeps that neutral statement or uses a sentence counsel writes. This repository does not contain a sentence that asserts independence.

### A.8 Sign-off checklist

- [ ] Approve `No failure found under the frozen contract.` as the `verified` receipt sentence, or write the replacement in the sign-off table.
- [ ] Confirm that sentence is limited to `verified`, and that `incomplete_fix_confirmed` and `inconclusive` do not use it.
- [ ] Approve the challenge-report sentences in A.3 for the challenge-report JWS, including the form with a period and the form without one, or require a single form.
- [ ] Confirm the receipt is not a warranty of security, merchantability, fitness, or future performance.
- [ ] Approve, reject, or replace the draft non-warranty sentence in A.4.
- [ ] Confirm the holdback (default 20 percent, 90 days, 7-day review SLA) is a payout term. Forfeit is limited to a security-motivated revert or an overlapping follow-up advisory. `unknown` holds the escrow for an operator. A non-security revert releases it.
- [ ] Approve the shipped payout strings in A.4, including "The merged fix was verified as incomplete, so this bounty will not be paid."
- [ ] Approve `verificationResult` `PASSED` for `verified` and `FAILED` for every other verdict, including `inconclusive`.
- [ ] Confirm readers of `FAILED` on an inconclusive result will be told that `FAILED` means the policy did not reach `verified`.
- [ ] Approve the VEX map: `verified` → `fixed`, `incomplete_fix_confirmed` → `affected`, `inconclusive` → `under_investigation`.
- [ ] Approve a `vex.justification` phrase, or record that launch publishes `status` only.
- [ ] Approve the house independence statement verbatim.
- [ ] Approve the neutral statement for `third_party` and `unknown`.
- [ ] Decide the statement that accompanies `verifier_independent_of_solver: true`.
- [ ] Confirm house receipts omit `verifier_independent_of_solver` and do not claim organizational independence.
- [ ] Confirm the word "secure" and the phrase "proven secure" are absent from receipt and challenge-report text.
- [ ] Confirm an embargoed receipt states the verdict only and omits the reproducer, the affected code, and the triggering steps until disclosure.

## Item B — Coordinated vulnerability disclosure and embargo

### B.1 Verbatim text of `SECURITY.md`

```text
# Security

## Reporting a vulnerability in SettleBridge

Email **security@a2a-settlement.org** if you find a vulnerability in SettleBridge itself. Describe what you tested, the version, and the impact. Do not open a public GitHub issue for a security report.

We aim to acknowledge receipt within 48 hours and to send an initial assessment within 7 days. For a critical vulnerability in a live SettleBridge deployment, we aim to deploy a fix within 72 hours of confirming it.

## Supported versions

Security fixes go to the current release of this repository (1.4.0 and later versions published from the main branch). Older snapshots do not receive security patches.

## Findings in other projects

Rules for findings the fix verifier makes in third-party code are in [docs/cyber/disclosure-policy.md](docs/cyber/disclosure-policy.md).
```

### B.2 Verbatim text of `docs/cyber/disclosure-policy.md`

```text
# Disclosure policy

This policy covers findings the SettleBridge fix verifier makes in someone else's code.

Launch scope is pure-Python projects published on PyPI.

## Opt-in

The verifier runs only on a repository whose owner has opted in. The repository link is enabled, and the owner has turned the verifier on. A repository that has not opted in is left alone.

## Reports on GitHub

The verifier opens a pull request only after the repository owner asks for one. The verifier opens a GitHub issue only after the repository owner asks for one.

## Private by default

A finding starts private. A confirmed incomplete fix in another project's code is an undisclosed vulnerability. The repository owner and SettleBridge operators can see the details in every state. Anyone else can see the details only after the finding is disclosed.

A finding moves from private, to notified, to embargoed, and then to disclosed. The maintainer can agree to publish from the notified state, before an embargo date is set.

## Embargo

We notify the maintainer, then keep the finding under embargo. The default embargo is 90 days from notification. The maintainer and SettleBridge can agree to a longer embargo. We publish earlier when the maintainer agrees. We publish earlier when the issue is being actively exploited.

## Receipts and reproducers

While a finding is embargoed, a receipt states the verdict only. The receipt leaves out the reproducer, the affected code, and the steps that trigger the bug. A reproducer is published only after the finding is disclosed.

## Contact

A maintainer reaches us at security@a2a-settlement.org. Use that address to confirm a notification, agree to publication, or ask for more time.

## Safe harbor

Good-faith research that follows this policy is authorized on a repository that has opted in. Keep the finding private, avoid harm to users, and avoid disruption of the service. SettleBridge will not bring a legal claim over that good-faith work. This safe harbor binds SettleBridge. Another party is bound when that party has agreed to the same terms. Research on a repository that has not opted in is outside this policy.
```

### B.3 Code behavior to read next to that policy

`backend/app/services/disclosure.py` applies to challenge findings:

- States are `private`, `notified`, `embargoed`, `disclosed`.
- Allowed moves: `private` → `notified`; `notified` → `embargoed` or `disclosed`; `embargoed` → `disclosed`. No move goes backward. `private` does not jump to `disclosed`. `disclosed` has no onward move.
- Entering `embargoed` requires `embargo_until` after `now`. When `embargo_until` is omitted, the embargo ends 90 days after `now` (`DEFAULT_EMBARGO_DAYS = 90`).
- A rejected transition leaves the finding unchanged.
- `can_view_details` is true for the task owner and for operator user ids in every state, and for anyone else only when the state is `disclosed`. An anonymous viewer is not the owner.
- `verifier_may_run` is true only when the repository link is enabled and `fix_verifier_opt_in` is true. The column `github_repo_links.fix_verifier_opt_in` defaults to false. The model comment states: "The fix verifier runs only when this is true and the link is enabled."

`fix_verifications.embargo_state` is a second field, on the verification row:

- The service creates the row with `embargo_state` `none`.
- On completion, `incomplete_fix_confirmed` sets `private`. Every other verdict sets `none`.
- The column default on the model is `private` if an insert omits the field. The service constructor does not omit it.
- `private` plus `incomplete_fix_confirmed` is the embargoed case. The installation owner and platform operators (`PLATFORM_ADMIN_USER_IDS`) see `result`. A funder who can read the row receives `result` replaced with `{"verdict": "<verdict>"}`.
- Any `embargo_state` other than `private` shows `result` to every reader who can read the row (installation owner, funder, or operator).
- The verification row does not store `notified`, `embargoed`, or `disclosed`. Those states exist on the finding record.

The fix-verifier service in this repository does not open a GitHub issue or a pull request.

Contact address in both documents: `security@a2a-settlement.org`.

Commitment language in `SECURITY.md` is "we aim to": acknowledge within 48 hours, initial assessment within 7 days, and a fix deployed within 72 hours of confirmation for a critical vulnerability in a live SettleBridge deployment. The disclosure policy's embargo clock is 90 days from notification unless the maintainer and SettleBridge agree otherwise. Earlier publication is stated for maintainer agreement and for active exploitation.

Safe harbor, as written, binds SettleBridge for good-faith research that follows the policy on an opted-in repository. It states that another party is bound when that party has agreed to the same terms, and that research on a repository that has not opted in is outside the policy.

### B.4 Sign-off checklist

- [ ] Approve `SECURITY.md` verbatim, including the 48-hour acknowledgement aim, the 7-day assessment aim, and the 72-hour aim for a critical fix on a live deployment.
- [ ] Approve `docs/cyber/disclosure-policy.md` verbatim, including opt-in, private-by-default, the 90-day embargo, earlier publication on maintainer agreement or active exploitation, receipt redaction, and safe harbor.
- [ ] Confirm the mailbox `security@a2a-settlement.org` is monitored against those aims.
- [ ] Accept the two-record model: finding states `private` / `notified` / `embargoed` / `disclosed`, and verification states `private` / `none`. Or require the verification row to use the same four states.
- [ ] Confirm `none` on a `verified` or `inconclusive` verification is the right visibility, because those verdicts are not a confirmed incomplete fix.
- [ ] Confirm "repository owner" in the policy is satisfied by the GitHub installation owner in the verification API, and that the funder sees only the verdict while `embargo_state` is `private`.
- [ ] Confirm the service's refusal to open a GitHub issue or pull request matches the policy sentence that those happen only after the owner asks.
- [ ] Confirm safe harbor is limited to SettleBridge, to opted-in repositories, and to research that follows the policy.
- [ ] Confirm launch scope in the policy (pure-Python projects on PyPI) matches the API's `unsupported_project` result for anything else.

## Item C — Model provider terms and exploit artifact storage

### C.1 What this repository stores

Challenge artifacts are content-addressed on the runner host. `CHALLENGE_ARTIFACT_ROOT` defaults to `/var/lib/settlebridge-challenges`. The config comment states: "Content-addressed artifact store on the runner host; never a production path."

`ArtifactKind` values are:

- `source_tarball`
- `history_bundle`
- `wheelhouse_manifest`
- `challenger_test`
- `observer_output`
- `control_output`
- `run_log`
- `report`

A challenge report excludes tokens, raw logs, and another tenant's data (A.3).

`fix_verifications.result` is a JSON object stored on the row. While an incomplete fix is `private`, readers other than the installation owner and operators receive only the verdict (B.3).

`verifier_bypass_submissions.reproducer` is a JSON object. The table also stores `summary`, `reward_cents`, and `decision_notes`. `VERIFIER_BYPASS_BOUNTY_ENABLED` defaults to false.

The disclosure policy requires that a reproducer stay unpublished until the finding is disclosed. Storage and publication are different acts. Counsel is asked whether each provider's terms allow the storage itself, under that publication rule.

This repository does not contain provider contracts.

### C.2 xAI

`assurance/assurance/providers/base.py` names provider id `xai` and candidate model id `grok-4.6`. Confirmatory runs refuse model ids that end in `-latest` or `-preview-latest`, and refuse `grok-latest` and `latest`.

`Settings` has no xAI key field and no xAI endpoint field.

Counsel reviews the current xAI terms for two acts: sending vulnerability and fix context to the provider, and retaining model-written tests or reproducers, including while a finding is embargoed.

### C.3 Anthropic Cyber Verification Program

`Settings` includes `ANTHROPIC_API_KEY` (empty by default) and `ASSIST_MODEL`, default `claude-sonnet-4-5-20250929`. That assist setting is separate from the fix-verifier flags.

The same candidate table names provider id `anthropic` and model id `claude-sonnet-4-6`.

Counsel reviews the current Anthropic Cyber Verification Program terms, and any commercial terms that apply if the program does not cover this use. The question is whether those terms allow generating security-test artifacts that may include a reproducer, and retaining those artifacts unpublished for the embargo in item B.

### C.4 Daybreak

This repository has no Daybreak setting, client, model id, or stored output.

Counsel's question, before any Daybreak output is retained: do Daybreak's terms allow storage of model-generated exploit or reproducer code? Until that sign-off is recorded, Daybreak output is not stored.

### C.5 Other provider ids in the public candidate table

The same table also names `openai` / `gpt-5-2025-08-07` and `gemini` / `gemini-2.5-pro`. They are listed so the public table is complete. The sign-off for this item is xAI, the Anthropic Cyber Verification Program, and Daybreak.

### C.6 Sign-off checklist

- [ ] xAI terms reviewed. Decision recorded on sending vulnerability and fix context, and on retaining tests and reproducers during an embargo.
- [ ] Anthropic Cyber Verification Program terms reviewed, plus the commercial terms that apply to `ANTHROPIC_API_KEY` and the named Claude model ids. Decision recorded on generating and storing security-test artifacts.
- [ ] Daybreak terms reviewed before any Daybreak output is stored. Storage of model-generated exploit or reproducer code is allowed only if the decision says so.
- [ ] Confirm stored reproducers stay unpublished until disclosure, matching item B.
- [ ] Confirm `verifier_bypass_submissions.reproducer` may be stored while a related finding is still private.
- [ ] Confirm challenge artifacts under `CHALLENGE_ARTIFACT_ROOT` are an acceptable store for `challenger_test`, `control_output`, `observer_output`, and `run_log`.
- [ ] Confirm logs and reports continue to exclude tokens, raw logs, and other tenants' data.
- [ ] Record the retention period and the deletion duty for provider outputs, if the terms impose one.

## Sign-off

| Item | Decision (approve / approve with changes / reject) | Replacement text, if any | Reviewer | Date |
| --- | --- | --- | --- | --- |
| A. Receipt wording, scope, VSA, VEX, independence | | | | |
| B. `SECURITY.md` and disclosure policy | | | | |
| C. xAI terms and artifact storage | | | | |
| C. Anthropic Cyber Verification Program terms and artifact storage | | | | |
| C. Daybreak terms and artifact storage | | | | |

Counsel notes:

```text

```
