# SettleBridge Training Harness

A thin, framework-agnostic orchestration loop that drives a registered agent through repeated training iterations on a SettleBridge bounty.

The harness speaks only the SettleBridge REST API. It has no access to the agent's internals — no prompt slots, no model parameters, no framework SDKs. The only lever it controls is the content of the `deliverable` dict sent to the submit endpoint on each iteration.

> This module lives in `settlebridge-ai/harness/` during initial development and will be extracted to its own package once the API contract stabilises.

---

## Installation

```bash
pip install httpx tenacity
# then copy harness.py into your project, or pip install -e harness/

# Optional: visualisation support
pip install 'settlebridge-harness[viz]'
```

---

## Quick start

```python
from harness import TrainingHarness

def my_callback(reasoning: str, diagnostics: dict, best_deliverable: dict) -> dict:
    """Receive Mediator feedback and the best deliverable so far; return the next one."""
    gaps = diagnostics.get("actionable_gaps", [])
    # Use best_deliverable as the starting point to avoid regressing from a bad iteration
    return {
        "content": f"Revised output addressing: {'; '.join(gaps)}",
        "format": "text",
    }

harness = TrainingHarness(
    api_url="https://settlebridge.ai",
    api_key="your-bearer-token",
    target_bounty_id="<bounty-uuid>",
    max_iterations=10,
    stake_budget=5000,       # total ATE across all iterations
    score_threshold=0.85,    # stop when Mediator confidence >= 0.85
    mutation_callback=my_callback,
    initial_deliverable={"content": "First attempt output", "format": "text"},
    task_type="summarization",
)

transcript = harness.run()
print(f"Final EMA:    {transcript['final_training_ema']:.4f}")
print(f"Best score:   {transcript['best_score']:.4f}  (iteration {transcript['best_iteration']})")
print(f"Merkle root:  {transcript['merkle_root']}")

# Save an interactive score trajectory chart
html = harness.plot(format="html")
with open("training_trajectory.html", "w") as f:
    f.write(html)
```

---

## `mutation_callback` contract

The callback is the only integration point between the harness and the agent:

```python
def mutation_callback(reasoning: str, diagnostics: dict, best_deliverable: dict) -> dict:
    ...
```

| Argument | Type | Description |
|---|---|---|
| `reasoning` | `str` | Plain-text Mediator explanation of why the deliverable scored as it did |
| `diagnostics` | `dict` | `{"task_type": str, "actionable_gaps": [str, ...], "details": dict or None}` |
| `best_deliverable` | `dict` | The deliverable that produced the highest score seen so far in this run |

The return value is used **verbatim** as the `deliverable` field of the next `POST /api/claims/{id}/submit` body. The harness does not inspect or validate it beyond JSON-serialisability.

**The harness cannot change the agent's internal configuration.** It can only change what it sends to the API. How the operator uses `actionable_gaps` to produce a better deliverable is entirely their responsibility.

---

## Keep/revert (evolutionary selection)

By default (`versioning=True`), the harness tracks the best-scoring deliverable seen across all iterations. After each iteration:

- **Keep** — if the new score exceeds the previous best, `best_deliverable` is updated.
- **Revert** — if the new score is equal to or lower, `best_deliverable` stays unchanged.

In both cases, `mutation_callback` always receives the current iteration's `reasoning` and `diagnostics` (so the agent knows what just happened), but `best_deliverable` always refers to the highest-scoring submission — never a regressed one.

```python
harness = TrainingHarness(
    ...
    versioning=True,   # default — keep/revert active
)
```

Set `versioning=False` to restore the original behaviour where `best_deliverable` always equals the most recent submission:

```python
harness = TrainingHarness(
    ...
    versioning=False,  # legacy — always mutate from latest
)
```

The transcript returned by `run()` includes three additional client-side fields:

| Field | Description |
|---|---|
| `best_score` | Highest numeric score achieved across all iterations |
| `best_iteration` | Iteration index that produced `best_score` |
| `improvement_history` | List of `{iteration_index, score, kept, cumulative_best, reasoning}` — one entry per iteration |

---

## Example: LLM-driven rewriter

```python
import openai

client = openai.OpenAI()

def llm_rewriter(reasoning: str, diagnostics: dict, best_deliverable: dict) -> dict:
    gaps = "\n".join(f"- {g}" for g in diagnostics.get("actionable_gaps", []))
    # Start from the best-so-far content to avoid regressing
    prior_content = best_deliverable.get("content", "")
    prompt = f"""The previous best submission was:

{prior_content}

The Mediator's latest feedback:
{reasoning}

Specific gaps to address:
{gaps}

Produce a revised, complete output that addresses all gaps while preserving
the strengths of the best previous submission."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    revised = response.choices[0].message.content
    return {"content": revised, "format": "text"}

harness = TrainingHarness(
    ...
    mutation_callback=llm_rewriter,
    ...
)
```

---

## Score trajectory visualisation

`plot()` generates a chart from the completed run showing raw scores, the running EMA line, the threshold, and keep/revert markers per iteration.

```python
transcript = harness.run()

# Interactive HTML (Plotly, default)
html = harness.plot(format="html")
with open("training_trajectory.html", "w") as f:
    f.write(html)

# Static PNG (matplotlib)
png_bytes = harness.plot(format="png")
with open("training_trajectory.png", "wb") as f:
    f.write(png_bytes)
```

Requires the `viz` extras:

```bash
pip install 'settlebridge-harness[viz]'
```

If neither Plotly nor matplotlib is installed, `plot()` raises an `ImportError` with the install command.

---

## Budget and stopping conditions

The harness stops when **any** of the following is true:

1. `numeric_score >= score_threshold` — success
2. `stake_spent >= stake_budget` — budget exhausted (raises `BudgetExhaustedError`)
3. `iterations >= max_iterations` — iteration cap reached
4. Score polling times out — score did not appear within `poll_timeout` seconds

**v1 limitation**: the budget ceiling is enforced client-side only. The server does not block a claim when `stake_spent >= stake_budget`. Because training runs are operator-funded (the operator is both requester and provider), overdraw has real ATE cost, which is a natural deterrent. A server-side check will be added in v2.

---

## Transcript

When the loop ends, the harness calls `POST /api/training/runs/{id}/complete` and then returns the transcript from `GET /api/training/runs/{id}/transcript`, augmented with client-side fields.

The server-side transcript is an immutable, Merkle-anchored document containing the full ordered attempt sequence, score trajectory, training EMA (computed with the same λ=0.1 as the exchange's production EMA), and a Merkle root over all provenance hashes. It is permanently associated with the agent's identity and publicly queryable.

The client-side augmentation adds `best_score`, `best_iteration`, and `improvement_history` to that dict before returning it to the caller.
