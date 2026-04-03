"""Mock Settlement Mediator for local training loop testing.

Scores improve +0.12 per call (capped at 0.95) so the harness
reaches the 0.85 threshold after ~4 iterations.

Run:
    uvicorn mock_mediator:app --port 9000
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Settlement Mediator")

_run_counts: dict[str, int] = {}  # keyed by run_id extracted from virtual escrow

GAPS_BY_ITERATION = [
    ["Revenue figure missing", "Net income not stated", "Guidance section absent"],
    ["Net income not stated", "Guidance section absent"],
    ["Guidance section absent"],
    [],
]


def _run_key(escrow_id: str) -> str:
    """Extract the training run UUID from a virtual escrow ID, or fall back to escrow_id."""
    if escrow_id.startswith("training:"):
        parts = escrow_id.split(":")
        # format: training:<run_id>:<iteration>:<suffix>
        return parts[1] if len(parts) >= 2 else escrow_id
    return escrow_id


@app.post("/mediate/{escrow_id}")
async def mediate(escrow_id: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    key = _run_key(escrow_id)
    n = _run_counts.get(key, 0)
    _run_counts[key] = n + 1

    score = round(min(0.95, 0.41 + n * 0.15), 4)
    gaps = GAPS_BY_ITERATION[min(n, len(GAPS_BY_ITERATION) - 1)]
    word_count = 280 + n * 5
    length_ok = word_count <= 300

    return JSONResponse({
        "escrow_id": escrow_id,
        "verdict": "approve" if score >= 0.85 else "reject",
        "confidence": score,
        "reasoning": (
            f"Iteration {n + 1}: score {score:.2f}. "
            + (f"Gaps: {'; '.join(gaps)}." if gaps else "All criteria met.")
        ),
        "structured_diagnostic": {
            "actionable_gaps": gaps,
            "details": {
                "omitted_points": gaps,
                "word_count": word_count,
                "length_ok": length_ok,
            },
        },
        "mode": body.get("mode", "production"),
        "task_type": body.get("task_type", "unknown"),
    })


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-mediator"}


@app.get("/audits/{escrow_id}")
async def audit(escrow_id: str):
    n = _call_counts.get(escrow_id, 0)
    return {"escrow_id": escrow_id, "calls": n}
