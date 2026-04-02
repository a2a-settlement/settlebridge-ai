# SettleBridge Training Harness

A thin, framework-agnostic orchestration loop that drives a registered agent through repeated training iterations on a SettleBridge bounty.

The harness speaks only the SettleBridge REST API. It has no access to the agent's internals — no prompt slots, no model parameters, no framework SDKs. The only lever it controls is the content of the `deliverable` dict sent to the submit endpoint on each iteration.

> This module lives in `settlebridge-ai/harness/` during initial development and will be extracted to its own package once the API contract stabilises.

---

## Installation

```bash
pip install httpx tenacity
# then copy harness.py into your project, or pip install -e harness/
```

---

## Quick start

```python
from harness import TrainingHarness

def my_callback(reasoning: str, diagnostics: dict) -> dict:
    """Receive Mediator feedback, return the next submission deliverable."""
    # The simplest possible callback: echo the gaps back as a note
    gaps = diagnostics.get("actionable_gaps", [])
    return {
        "content": f"Revised output addressing: {'; '.join(gaps)}",
        "format": "text",
    }

harness = TrainingHarness(
    api_url="https://app.settlebridge.ai",
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
print(f"Final EMA: {transcript['final_training_ema']:.4f}")
print(f"Merkle root: {transcript['merkle_root']}")
```

---

## `mutation_callback` contract

The callback is the only integration point between the harness and the agent:

```python
def mutation_callback(reasoning: str, diagnostics: dict) -> dict:
    ...
```

| Argument | Type | Description |
|---|---|---|
| `reasoning` | `str` | Plain-text Mediator explanation of why the deliverable scored as it did |
| `diagnostics` | `dict` | `{"task_type": str, "actionable_gaps": [str, ...], "details": dict or None}` |

The return value is used **verbatim** as the `deliverable` field of the next `POST /api/claims/{id}/submit` body. The harness does not inspect or validate it beyond JSON-serialisability.

**The harness cannot change the agent's internal configuration.** It can only change what it sends to the API. How the operator uses `actionable_gaps` to produce a better deliverable is entirely their responsibility.

---

## Example: LLM-driven rewriter

```python
import openai

client = openai.OpenAI()

def llm_rewriter(reasoning: str, diagnostics: dict) -> dict:
    gaps = "\n".join(f"- {g}" for g in diagnostics.get("actionable_gaps", []))
    prompt = f"""The previous submission received this feedback:

{reasoning}

Specific gaps to address:
{gaps}

Produce a revised, complete output that addresses all gaps."""

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

## Budget and stopping conditions

The harness stops when **any** of the following is true:

1. `numeric_score >= score_threshold` — success
2. `stake_spent >= stake_budget` — budget exhausted (raises `BudgetExhaustedError`)
3. `iterations >= max_iterations` — iteration cap reached
4. Score polling times out — score did not appear within `poll_timeout` seconds

**v1 limitation**: the budget ceiling is enforced client-side only. The server does not block a claim when `stake_spent >= stake_budget`. Because training runs are operator-funded (the operator is both requester and provider), overdraw has real ATE cost, which is a natural deterrent. A server-side check will be added in v2.

---

## Transcript

When the loop ends, the harness calls `POST /api/training/runs/{id}/complete` and then returns the transcript from `GET /api/training/runs/{id}/transcript`.

The transcript is an immutable, Merkle-anchored document containing the full ordered attempt sequence, score trajectory, training EMA (computed with the same λ=0.1 as the exchange's production EMA), and a Merkle root over all provenance hashes. It is permanently associated with the agent's identity and publicly queryable.
