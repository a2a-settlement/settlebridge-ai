# Evaluation corpus

PyPI fixes for the challenge engine's go/no-go numbers. `manifest.json` is the corpus. `schema.json` is its JSON Schema (draft 2020-12).

`held_out: true` means the entry must not be used to tune prompts. `sb-pr32` is the only entry with `held_out: false`.

Commit fields are 40 lowercase hex or null. What they mean depends on the category:

- `incomplete_fix`: `fix_commit` is the first fix, which a later advisory says was incomplete or bypassed. `base_commit` is its first parent. `followup_fix_commit` is the fix that completed it, or null when no fixed release exists. The challenge targets `fix_commit`.
- `complete_fix`: `fix_commit` is the fix and `base_commit` its first parent. No later pip advisory cites the fix's CVE.
- `regression`: `fix_commit` is the change that introduced the flaw, named by the advisory, and `base_commit` its first parent. The observer must be silent on `base_commit` and fire on `fix_commit`. `failure_class` is the flaw's class, not `regression`.

Entries are pure Python, or have Python 3.11 wheels, because the sandbox image is `python:3.11-slim` with no compiler and no `git`.

## Verification

Each entry was checked against GitHub's reviewed pip advisories and a local clone of its repository:

- The category comes from the advisory text: "incomplete fix for CVE-…", "bypass of the fix", or "introduced in" a named commit or release. For `complete_fix`, no later pip advisory cites the CVE.
- Every commit resolves in its own repository, `base_commit` is the first parent, and `followup_fix_commit` descends from `fix_commit`.

The checker fills:

- `verification.checked_at`
- `verification.checked_by`
- `verification.evidence_urls` (the advisories and the commits)

and sets `verified` to true. The harness will not score a result that names an unverified entry.

## Run

From `challenge-runner/`:

```bash
/home/rich/miniconda3/envs/settlebridge_ai/bin/python corpus/harness.py \
  --manifest corpus/manifest.json \
  --results results.jsonl \
  --allow-unverified
```

`--allow-unverified` is required while entries are unverified. It stamps the text report and the JSON report with `UNVERIFIED — not a go/no-go result`. That output is not a decision.

Exit 2 when unverified entries are scored without the flag. Exit 1 when the manifest or the results file is malformed. Exit 0 when a report is printed, including when the criteria fail.

## Results JSONL

One object per line:

- `entry_id`
- `arm`: `tests_and_scanners`, `second_model_review`, or `seeded_challenger`
- `verdict`: `confirmed`, `no_failure_found`, `inconclusive`, or `invalid_or_duplicate`
- `audit`: `agree`, `disagree`, or null
- `controls_ok`: boolean
- `oracle_tamper_accepted`: boolean
- `compute_cents`: non-negative integer

One row per entry and arm.

## Metrics

Reported per arm. The population is the rows for that arm.

- Confirmed rate: confirmed verdicts on `incomplete_fix` rows, divided by those rows.
- False confirms: confirmed rows whose category is `complete_fix`, or whose audit is `disagree`. A row that meets both counts once.
- Blind-audit precision: `agree` divided by `agree` plus `disagree`, among confirmed rows. Confirms with a null audit are not in this ratio.
- Inconclusive rate: `inconclusive` verdicts divided by scored rows.
- Controls-correct rate: rows with `controls_ok` true, divided by scored rows.
- Accepted tamper: rows with `oracle_tamper_accepted` true.
- Median compute cents: median of `compute_cents` on confirmed rows. An even count uses the mean of the two middle values.
- Equal compute: an arm is flagged when its total `compute_cents` differs from `seeded_challenger` by more than 10 percent. Exactly 10 percent is not flagged. The flag is printed and is not a go/no-go gate.

## Go/no-go

The gate is `seeded_challenger` only. All of these must hold. The report prints each one as `PASS` or `FAIL` with the measured value.

- Confirmed rate on incomplete fixes >= 50%.
- Blind-audit precision >= 0.9.
- False confirms = 0.
- At least 2 more confirmed verdicts than `second_model_review` (every confirmed verdict, not only incomplete fixes).
- Inconclusive rate <= 20%.
- Controls-correct rate >= 95%.
- Accepted tamper = 0.
- Median compute cents per confirmed finding <= 2500.
- At least 20 scored rows, including at least 6 incomplete, 6 complete, and 3 regression.

Ratios use integer cross-multiplication, so a value on the threshold passes.
