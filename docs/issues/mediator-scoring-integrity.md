# Mediator scoring & approval integrity: non-compliant deliverables scored as compliant and auto-approved

- **Status:** Open
- **Severity:** High (economic loss + corrupted training signal)
- **Components:** `backend/app/services/review_service.py`, `backend/app/services/training_service.py`, `backend/app/routes/submissions.py`, `backend/app/services/score_history_write.py`, `harness/harness.py`
- **Environment:** production — `market.settlebridge.ai`
- **Repo revision reviewed:** `2ad5948` (`refactor(review): extract parse_review_response for local eval reuse.`)
- **Observed:** 2026-09-07
- **Filed by:** investigation task (documentation only; no code changed)

> Note on repo conventions: this repo has no `docs/` directory, no `.github/ISSUE_TEMPLATE`, and no
> pre-existing issue-report convention at the reviewed revision. `docs/issues/` was created for this
> report. If a convention is later adopted, this file should be moved to match it.

---

## 1. Summary

Two production bounties were closed and settled on deliverables that the platform should not have
accepted.

1. **Defect 1 (primary).** The AI reviewer ("Mediator") asserted in its own reasoning that two
   required fields were present in a deliverable when the server's stored copy of that deliverable
   does not contain them. It awarded **92/100**, recommended **`approve`**, and reported
   **zero** issues. The bounty was then closed as `completed` and the submission marked `approved`.
   The deliverable satisfied **4 of 6** required top-level keys.

2. **Defect 2 (secondary, same code path).** A training run whose *every* iteration scored
   **15/100** with recommendation **`reject`** still ended with one submission marked `approved`
   and the parent bounty closed as `completed`. The winner was chosen by a tie-break that (a) never
   consults `ai_review.recommendation`, (b) never consults the run's own `score_threshold`, and
   (c) breaks ties in the opposite direction from the harness client.

Both approvals were performed by the same function, `training_service.complete_run`, and **both
bounties had `auto_approve: false`**. The requesters had explicitly opted into manual review and the
platform approved anyway. That control bypass is arguably the most serious finding in this report and
was not part of the original defect description.

## 2. Impact

**Economic.** Escrow settlement is driven off submission/bounty state. Two 100 ATE bounties were
sealed `completed` with an `approved` submission — one on work missing a third of its required
fields, one on work the reviewer itself scored 15/100 and recommended rejecting. On the non-training
auto-approve path an `approve` recommendation triggers `exchange_svc.release_escrow` directly
(`backend/app/routes/submissions.py:238-241`), so the same hallucination class releases real escrow
whenever `auto_approve` is enabled.

**Requester control bypass.** `auto_approve: false` is the requester's instruction that a human
decides. `complete_run` ignores that flag entirely and approves regardless. Any bounty attached to a
training run is effectively `auto_approve: true` without the requester's consent.

**Trust / training-loop corruption.** This is the deeper cost. The score is the reward signal for the
self-improving harness:

- `mediator_result_from_ai_review` converts the LLM score to `numeric_score` by `score / 100.0`
  (`backend/app/services/score_history_write.py:16-20`), so the hallucinated 92 became `0.92`.
- `diagnostics.actionable_gaps` is copied straight from `ai_review["issues"]`
  (`score_history_write.py:25`), which was `[]`. The agent was told it had **no** gaps while it was
  in fact violating two hard requirements.
- That score then feeds `compute_ema` and is sealed into the signed transcript and Merkle root
  (`training_service.py:221-224`, `243-249`).

So the harness receives a strong positive gradient for producing a non-compliant artifact, and the
Merkle-rooted transcript — the platform's integrity artifact — attests to a score that is provably
wrong against data the platform itself stores. A score that cannot be trusted as a compliance signal
makes the entire training-harness feedback loop unsound, and makes published transcripts misleading
to third parties.

---

## 3. Defect 1 — Mediator hallucinated required fields and approved a non-compliant deliverable

### 3.1 Identifiers

| Field | Value |
| --- | --- |
| Bounty | `0da7254b-ca2a-4142-b560-fc7c114eb77e` — "NVDA 22-Day Ensemble Forecast — AlphaSignal Multi-Model" |
| Reward | 100 ATE |
| `auto_approve` | `false` |
| `max_claims` | `1` |
| `provenance_tier` | `tier1_self_declared` |
| Training run | `cb667502-8d57-4cf1-97a4-4f07350ef804` |
| Transcript | `4374a56c-dfc2-47ac-a52f-a9720a50cc5c` |
| Submission | `8634fa7f-f114-4fba-b627-d6e1937c75f1` |
| Score row | `709bf43d-a13d-4ae7-beb6-8234f15f60ae` (`numeric_score` 0.92) |
| Score row created | 2026-09-07T18:57:36.075811Z |
| Bounty closed | 2026-09-07T18:57:45.243260Z (`completed`) |

### 3.2 Reproduction (read-only, no state mutation)

Completed bounties and their submissions are publicly readable
(`backend/app/routes/submissions.py:346-351`), so this is verifiable without credentials:

```bash
curl -s https://market.settlebridge.ai/api/bounties/0da7254b-ca2a-4142-b560-fc7c114eb77e
curl -s https://market.settlebridge.ai/api/bounties/0da7254b-ca2a-4142-b560-fc7c114eb77e/submissions
```

Then compare the reviewer's claims against the stored deliverable:

```python
import json, urllib.request

B = "0da7254b-ca2a-4142-b560-fc7c114eb77e"
subs = json.load(urllib.request.urlopen(
    f"https://market.settlebridge.ai/api/bounties/{B}/submissions"))
sub = subs[0]
content = json.loads(sub["deliverable"]["content"])

required = ["ticker", "forecast_horizon_days", "last_close",
            "forecast_bands", "model_weights", "generated_by"]
print({k: k in content for k in required})
print("any band has p50:", any("p50" in b for b in content["forecast_bands"]))
print(sub["ai_review"]["score"], sub["ai_review"]["recommendation"],
      sub["ai_review"]["issues"], sub["reviewer_notes"])
```

### 3.3 Evidence

The bounty's `acceptance_criteria.description` requires six top-level keys:

```
Deliverable must be valid, non-empty JSON (parseable by json.loads) with ALL of the
following top-level keys:
• ticker (string, must equal 'NVDA')
• forecast_horizon_days (integer, must equal 22)
• last_close (number, USD price)
• forecast_bands (object: per-day entries each with numeric p10, p25, p50, p75, p90)
• model_weights (object: model names → non-negative floats summing to 1.0)
• generated_by (string, must identify AlphaSignal ensemble agent)
```

**The reviewer's stored `ai_review.notes`** (emphasis added):

> "Deliverable is valid, well-formed JSON containing **all required top-level keys** with correct
> data types and values. ticker='NVDA', forecast_horizon_days=22, last_close=228.45 (numeric USD),
> forecast_bands with 22 entries each containing p10, p25, **p50**, p75, p90 (all numeric),
> model_weights sum to 1.0 with five named models (ou, monte_carlo, sarima, lstm, xgboost), and
> **generated_by identifies AlphaSignal ensemble methodology**."

**The server's own stored copy of that submission**, verified independently via the public endpoint
above:

```
required 'ticker' present:                True
required 'forecast_horizon_days' present: True
required 'last_close' present:             True
required 'forecast_bands' present:         True
required 'model_weights' present:          True
required 'generated_by' present:          False   <-- claimed present
n bands: 22
band0 keys: ['day', 'mean', 'p10', 'p25', 'p75', 'p90']
ANY band has p50:                         False   <-- claimed present, "all numeric"
model_weights sum: 1.0
```

Resulting platform state:

```
ai_review.score:  92
recommendation:   approve
holdback:         False
issues:           []
submission.status: approved
submission.score:  None
reviewer_notes:   [Auto-resolved at run completion — highest scoring submission]
bounty.status:     completed
```

Two claims in the reasoning are false against data the platform stores. Four of six required keys
were satisfied. The reviewer reported zero issues and recommended full approval.

The specific hallucination is a **plausible-neighbour substitution**, which matters for the fix: the
deliverable contains `mean` (not `p50`) in each band, and contains `generated_at`, `forecast_method`,
and `methodology` (not `generated_by`). The reviewer substituted the semantically adjacent key it
expected to see. Exhaustive key-presence enumeration is precisely the class of check LLMs are
unreliable at and that is trivial and total in code.

Note also `submission.score` is `None` — the 92 exists only inside the `ai_review` JSON blob, so the
first-class score column carries no record of the basis for approval.

### 3.4 Root cause

**RC1-A — Acceptance-criteria compliance is judged entirely by an LLM. There is no deterministic
verification anywhere in the codebase.**

`review_deliverable` (`backend/app/services/review_service.py:200-251`) is the only evaluation of a
deliverable against `acceptance_criteria`. It builds a prompt and returns whatever the model says.
`_build_prompt` (`review_service.py:138-179`) serialises the criteria into prompt text and nothing
more:

```154:154:backend/app/services/review_service.py
        parts.append(f"\n### Acceptance Criteria\n{json.dumps(acceptance_criteria, indent=2)}")
```

`parse_review_response` (`review_service.py:182-197`) is the only post-processing. It validates the
*envelope*, never the *claims*:

```193:196:backend/app/services/review_service.py
    required_keys = {"score", "recommendation", "holdback", "notes"}
    if not required_keys.issubset(review.keys()):
        return {}
    review["score"] = max(0, min(100, int(review["score"])))
```

It clamps the score to 0–100 and returns. At no point is the deliverable parsed and compared to the
criteria. A repo-wide search for validation of criteria keys (`forecast_bands`, `p50`, JSON-Schema,
any required-key check) returns nothing in `backend/app/`. **Compliance is 100% LLM judgment.**

**RC1-B — The only programmatic gate on the submit path was a no-op for this bounty.**

`submit_work` calls exactly one validator before accepting work
(`backend/app/routes/submissions.py:90-92`): `validate_provenance`. That function inspects provenance
*metadata*, never deliverable content, and short-circuits to zero errors for this bounty's tier:

```17:18:backend/app/services/submission_service.py
    if required_tier == ProvenanceTier.TIER1_SELF_DECLARED:
        return errors
```

The bounty is `tier1_self_declared`, so the submit path performed no content validation at all.

**RC1-C — The criteria schema offers no machine-readable slot, so the requirements could not have
been checked even in principle.**

```11:16:backend/app/schemas/bounty.py
class AcceptanceCriteria(BaseModel):
    description: str = ""
    output_format: str = ""
    required_sources: list[str] | None = None
    provenance_tier: ProvenanceTier = ProvenanceTier.TIER1_SELF_DECLARED
    custom_checks: list[dict] | None = None
```

The requester expressed all six machine-checkable requirements inside the free-text `description`,
and `custom_checks` was `null`. That was the only option available: `custom_checks` is **never read
by any executing backend code**. It appears only in schema definitions (`schemas/bounty.py:16`,
`schemas/assist.py:55`, `schemas/contract.py:17`), in seed data (`seed.py:99`), and in the
bounty-drafting prompt (`services/prompts/bounty_assist.py:52`). No validator consumes it. A
requester has no supported way to express an enforceable constraint, which is why these requirements
were only ever natural-language text handed to a model.

**RC1-D — The approval itself did not come from the auto-approve path, and bypassed
`auto_approve: false`.**

This is the part that most needs fixing. Because `bounty.auto_approve` is `false`, the auto-approval
block at `backend/app/routes/submissions.py:168` never executed. The `approved` status came from
`training_service.complete_run`, as its own `reviewer_notes` string proves
(`[Auto-resolved at run completion — highest scoring submission]`, set at
`training_service.py:299`). `complete_run` never reads `bounty.auto_approve`; it approves the
top-ranked pending submission unconditionally and then seals the bounty:

```322:324:backend/app/services/training_service.py
    if parent_bounty is not None and parent_bounty.status != BountyStatus.COMPLETED:
        parent_bounty.status = BountyStatus.COMPLETED
        parent_bounty.completed_at = now
```

This is the same mechanism as Defect 2. Defects 1 and 2 are one settlement bug with two different
inputs.

---

## 4. Defect 2 — run-completion tie-break approved a `reject`-recommendation deliverable

### 4.1 Identifiers

| Field | Value |
| --- | --- |
| Bounty | `b2b60c92-ae0b-4fba-af03-00a3a6a9f085` — "AlphaSignal Ensemble: SPY 22-Day Forecast" |
| `auto_approve` | `false` |
| `acceptance_criteria` | **`null`** |
| Training run | `dcc4940e-2677-47e2-bbf8-0530a3d1291a` (2 iterations) |
| Submission iter 1 | `9bf81ffa-b1f5-4d34-9704-02e1d6a40f7f` — 18:14:41.861221Z |
| Submission iter 2 | `0239f3f8-4175-4b77-a461-03e7f0336977` — 18:15:10.448645Z |
| Bounty closed | 2026-09-07T18:15:21.473139Z (`completed`) |

Verified current state:

```
0239f3f8 (iter 2, LATER)   status: approved   ai_review 15 / reject
                           reviewer_notes: [Auto-resolved at run completion — highest scoring submission]
9bf81ffa (iter 1, EARLIER) status: rejected   ai_review 15 / reject
                           reviewer_notes: [Auto-resolved at run completion — lower scoring submission]
bounty.status: completed
```

A 100 ATE bounty is closed `completed` on a 15/100 deliverable whose own AI recommendation was
`reject`. Note this bounty had **no acceptance criteria at all** (`acceptance_criteria: null`), so
there was nothing for even an LLM to check against.

### 4.2 Root cause — exact logic

**Ordering** (`backend/app/services/training_service.py:277-287`):

```283:286:backend/app/services/training_service.py
        .order_by(
            func.cast(Submission.ai_review["score"].as_string(), Integer).desc(),
            Submission.submitted_at.desc(),
        )
```

On the 15/15 score tie, `submitted_at DESC` selects the **later** submission — iteration 2.

**Auto-approve condition** (`training_service.py:291-311`). The recommendation *is* read, then
ignored for the winner:

```291:301:backend/app/services/training_service.py
    for i, sub in enumerate(pending):
        claim = (await db.execute(select(Claim).where(Claim.id == sub.claim_id))).scalar_one()
        rec = (sub.ai_review or {}).get("recommendation", "reject")
        sub.reviewed_at = now

        if i == 0:
            # Best-scoring submission — mark as approved
            sub.status = SubmissionStatus.APPROVED
            sub.reviewer_notes = "[Auto-resolved at run completion — highest scoring submission]"
            claim.status = ClaimStatus.ACCEPTED
            claim.resolved_at = now
```

`rec` is computed at line 293 but is only consulted in the `elif rec == "partial_approve"` branch at
line 302, which is unreachable when `i == 0`. **`recommendation` is never consulted before approving
and closing the bounty.** Answering the question directly: no, `recommendation == 'reject'` is not
checked at all on the winning path.

**`score_threshold` is also never consulted.** The run carries a `score_threshold` (default `0.85`,
`backend/app/routes/training.py:35`), and 0.15 is far below it. `complete_run` contains no reference
to it. The threshold is used in only two places: gating real-escrow release/refund on the submit path
(`backend/app/routes/submissions.py:309-315`, skipped for virtual training escrows), and as a
display-only `threshold_reached` field on the run card (`backend/app/routes/training.py:473`). It
never gates approval.

**Tie direction disagrees with the harness client.** `harness/harness.py:575` uses a strict `>`:

```575:579:harness/harness.py
                kept = last_score > self._best_score
                if kept:
                    self._best_score = last_score
                    self._best_deliverable = copy.deepcopy(deliverable)
                    self._best_iteration = iteration
```

`_best_iteration` advances only when `kept` is true, so on a tie the harness keeps the **earlier**
iteration and reported `best_iteration: 1` (transcript field set at `harness/harness.py:682`), while
the server approved iteration 2. Client and server disagree on which artifact won the run — the
transcript and the settlement point at different submissions.

### 4.3 Related latent issue (not yet observed in production)

The ordering casts a JSON value to `Integer`:
`func.cast(Submission.ai_review["score"].as_string(), Integer)`. The query filters on
`Submission.ai_review.isnot(None)` (`training_service.py:282`) but not on the presence of a `score`
*key*. On PostgreSQL 16 (the deployed backend), `ORDER BY ... DESC` defaults to **NULLS FIRST**, so a
submission carrying an `ai_review` object with no `score` key would sort **first** and be
auto-approved. Separately, a non-integer score (e.g. `92.5`) would raise a cast error and fail
`complete_run` — `parse_review_response` coerces via `int(...)` on the normal path, but any review
object written by another path is not guaranteed integral.

---

## 5. Platform defect vs. submitting agent's own gap

Keeping these separate matters, because fixing the agent does not fix the platform.

**The submitting agent's own bug (real, being fixed separately, not a platform issue):**

- The NVDA deliverable genuinely omitted `generated_by`.
- Its `forecast_bands` entries genuinely used `mean` instead of the required `p50`.
- It therefore satisfied only 4 of 6 required top-level keys and was correctly non-compliant.

**Platform defects (this issue):**

1. A machine-checkable criteria violation was scored 92/100 with recommendation `approve` and zero
   reported issues — the reviewer asserted the presence of two fields that are absent from the
   server's own stored copy.
2. There is no deterministic validation of machine-checkable acceptance criteria anywhere; compliance
   is entirely LLM judgment (§3.4 RC1-A).
3. The criteria schema provides no executable slot for such constraints — `custom_checks` is
   accepted, stored, and never read (§3.4 RC1-C).
4. `complete_run` approves and closes bounties with `auto_approve: false`, overriding the requester's
   explicit choice of manual review.
5. `complete_run` approves the top-ranked submission without consulting
   `ai_review.recommendation` or `run.score_threshold`, so a 15/100 `reject` was approved.
6. The server's tie-break direction contradicts the harness client's, so transcript
   `best_iteration` and the approved submission can disagree.
7. `actionable_gaps` is copied verbatim from a hallucinating reviewer's `issues: []`, feeding a
   false "no gaps" signal into the training loop.
8. No test covers `complete_run`'s resolution logic (see §7).

---

## 6. Recommended fixes

Ranked by impact-per-unit-risk. Items 1–2 are small, surgical, and stop ongoing loss; item 3 is the
real correctness fix.

**1. Refuse to auto-approve when the reviewer did not recommend approval.** In `complete_run`
(`training_service.py:291-311`), gate the `i == 0` branch on `rec != "reject"` and on
`sub_score >= run.score_threshold * 100`. When the best submission fails the gate, mark it rejected
(or leave it pending) and do **not** set `parent_bounty.status = COMPLETED`
(`training_service.py:322-324`). Directly fixes Defect 2(a) and prevents the Defect 1 class from
settling whenever the reviewer does flag a problem.

**2. Do not let `complete_run` approve anything when `bounty.auto_approve is False`.** Leave such
submissions `PENDING_REVIEW` for the requester and seal the bounty without an approval, or introduce
an explicit `AWAITING_REVIEW` terminal state for the run. This restores the requester control that is
currently bypassed, and would have prevented **both** production incidents on its own.

**3. Add deterministic pre-validation of machine-checkable acceptance criteria, and keep compliance
separate from the qualitative score.** Concretely:

- Make `custom_checks` (or a new `schema` field on `AcceptanceCriteria`) a real, executed contract —
  JSON-Schema is the natural fit and covers every requirement in this bounty: required top-level
  keys, per-entry required keys, numeric types, `const` for `ticker`/`forecast_horizon_days`, and a
  weights-sum assertion.
- Run it in `submit_work` before/alongside `review_deliverable`
  (`backend/app/routes/submissions.py:139-156`) and persist the result as a first-class
  `compliance` object on the submission, distinct from `ai_review.score`.
- Treat a compliance failure as an authoritative override: never `approve`, never release escrow, and
  never seal the bounty on a hard-criteria failure regardless of what the LLM said. The LLM should
  grade *quality*; code should decide *compliance*.
- Feed the criteria's own `description` through the bounty-assist path so requesters get a generated
  machine-checkable schema, rather than requirements that exist only as prose.

**4. Merge deterministic failures into `actionable_gaps`.** In `score_history_write.py:24-27`, union
the LLM's `issues` with the compliance checker's failures, so the harness receives
`missing required key 'generated_by'` and `forecast_bands[*] missing 'p50'` as real gradient instead
of `[]`. Without this, fixing approval still leaves the training loop learning from a false signal.

**5. Align the tie-break direction with the harness.** Change
`Submission.submitted_at.desc()` to `.asc()` at `training_service.py:285` so score ties keep the
**earlier** submission, matching the harness's strict `>` at `harness/harness.py:575`. Better still,
define "best" once in a shared helper so client and server cannot drift.

**6. Harden the ordering query.** Filter for the presence of a numeric `score` key, cast to
`Numeric`/`Float` rather than `Integer`, and add an explicit `NULLS LAST` to the `DESC` clause so a
score-less `ai_review` can never sort into the winning position on PostgreSQL.

**7. Harden the review prompt (mitigation, not a fix).** `REVIEW_SYSTEM` already requires the model
to quote exact values when alleging a contradiction
(`backend/app/services/review_service.py:97-102`). Apply the same discipline in the other direction:
require the reviewer to enumerate every required key with an explicit present/absent verdict and to
quote the JSON path where it found each one. This raises the cost of a plausible-neighbour
substitution such as `generated_at` → `generated_by` or `mean` → `p50`, but it is a probabilistic
mitigation and must not be treated as a substitute for item 3.

**8. Consider refusing to seal a bounty whose `acceptance_criteria` is `null`,** or at minimum
surfacing it, since bounty `b2b60c92` was reviewed and settled with no criteria to check against.

---

## 7. Test gap

There is no test coverage for `complete_run`'s submission-resolution logic. `backend/tests/` contains
only `test_review_prompt.py`, `test_score_history_write.py`, and `test_skill_evidence.py`; a search
for the `[Auto-resolved at run completion` marker finds it only in
`training_service.py:299,304,309` and in no test. Any fix should land with cases for: score tie →
which submission wins; winner with `recommendation == "reject"`; winner below `score_threshold`;
`bounty.auto_approve is False`; and `ai_review` present but missing `score`.

Suggested regression fixtures, both drawn from real data above:

- Deliverable missing `generated_by` and using `mean` instead of `p50`, with an `ai_review` of
  `{score: 92, recommendation: "approve", issues: []}` → must **not** approve, must **not** seal the
  bounty, and must report both missing keys in `actionable_gaps`.
- Two submissions tied at `score: 15` with `recommendation: "reject"` → must approve **neither**, and
  if a winner must be chosen for reporting purposes it must be the **earlier** one, matching
  `best_iteration` in the harness transcript.

---

## 8. Verification notes

All submission and bounty facts in this report were re-verified against production at the time of
writing using unauthenticated `GET` requests only (completed bounties are publicly readable per
`backend/app/routes/submissions.py:346-351`). No marketplace state was mutated, no claim or
submission was created, and no code was changed.

One correction to the originally reported line numbers, for anyone following up: the tie-break is at
`backend/app/services/training_service.py:283-286` (with the approval branch at `291-311`), not at
`:87-89`; line 87 is inside `create_run`. The `_submission_id` stamping citation at
`backend/app/services/score_history_write.py:50` is accurate.

## 9. Filing

The repo remote is `git@github.com:a2a-settlement/settlebridge-ai.git`. There is no
`.github/ISSUE_TEMPLATE`, so no template applies. This issue has **not** been filed. The command that
would file it:

```bash
gh issue create \
  --repo a2a-settlement/settlebridge-ai \
  --title "Mediator scored a non-compliant deliverable 92/100 and auto-approved it; run-completion tie-break approves 'reject' submissions" \
  --body-file docs/issues/mediator-scoring-integrity.md \
  --label bug
```

The `bug` label exists on the repo. No `severity:*`, `security`, or `area:*` labels exist yet, so none
are applied.
