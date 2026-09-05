"""Unit tests for the SettleBridge training harness."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from harness import (
    MutationContext,
    MutationResult,
    RejectedFeedback,
    TrainingHarness,
    default_candidate_identity,
)


BOUNTY = "bounty-1"
API = "https://sb.test"


def _harness(**kwargs) -> TrainingHarness:
    defaults = dict(
        api_url=API,
        api_key="tok",
        target_bounty_id=BOUNTY,
        max_iterations=3,
        stake_budget=1000,
        score_threshold=0.99,
        mutation_callback=kwargs.pop("mutation_callback", lambda r, d, b: b),
        initial_deliverable={"content": "v1", "format": "text"},
        poll_interval=0.0,
        poll_timeout=0.4,
    )
    defaults.update(kwargs)
    return TrainingHarness(**defaults)


def _mount_common(router: respx.MockRouter, *, run_id: str = "run-1") -> None:
    router.post(f"{API}/api/training/runs").mock(
        return_value=httpx.Response(200, json={"run_id": run_id})
    )
    router.post(f"{API}/api/training/runs/{run_id}/complete").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_iterations": 1,
                "final_training_ema": 0.7,
                "merkle_root": "abc",
            },
        )
    )
    router.get(f"{API}/api/training/runs/{run_id}/transcript").mock(
        return_value=httpx.Response(
            200, json={"final_training_ema": 0.7, "merkle_root": "abc"}
        )
    )
    router.get(f"{API}/api/training/runs/{run_id}").mock(
        return_value=httpx.Response(200, json={"stake_spent": 100})
    )
    router.post(f"{API}/api/bounties/{BOUNTY}/claim").mock(
        return_value=httpx.Response(200, json={"id": "claim-1"})
    )


@respx.mock
def test_poll_binds_to_submission_id_not_latest_row():
    _mount_common(respx)
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(f"{API}/api/claims/claim-1/submit").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        items = [
            {
                "numeric_score": 0.11,
                "reasoning": "old",
                "diagnostics": {"_submission_id": "sub-older", "actionable_gaps": []},
            },
            {
                "numeric_score": 0.72,
                "reasoning": "new",
                "diagnostics": {
                    "_submission_id": "sub-1",
                    "actionable_gaps": ["gap"],
                },
            },
        ]
        return httpx.Response(200, json={"items": items})

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)

    def cb(reasoning, diagnostics, best_deliverable):
        return MutationResult(
            deliverable={"content": "v2", "format": "text"},
            patched=False,
        )

    h = _harness(mutation_callback=cb, max_iterations=2)
    transcript = h.run()
    assert transcript["improvement_history"][0]["score"] == 0.72
    assert transcript["improvement_history"][0]["score_source"] == "score_history"


@respx.mock
def test_score_source_locked_ai_review_ignores_later_score_history():
    _mount_common(respx)
    n = {"submit": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        n["submit"] += 1
        return httpx.Response(200, json={"id": f"sub-{n['submit']}"})

    respx.post(f"{API}/api/claims/claim-1/submit").mock(side_effect=on_submit)

    # No matching score-history on iter 1 → lock ai_review.
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    def on_sub(request: httpx.Request) -> httpx.Response:
        sid = request.url.path.rsplit("/", 1)[-1]
        score = 72 if sid == "sub-1" else 10
        return httpx.Response(
            200,
            json={"ai_review": {"score": score, "notes": f"ai {sid}", "issues": []}},
        )

    respx.get(url__regex=r".*/api/submissions/sub-\d+$").mock(side_effect=on_sub)

    def cb(reasoning, diagnostics, best_deliverable):
        return {"content": "v2", "format": "text"}

    h = _harness(mutation_callback=cb, max_iterations=2, poll_timeout=0.2)
    # After lock, iter 2 still has empty score-history; ai_review should be used.
    transcript = h.run()
    assert transcript["score_source"] == "ai_review"
    assert transcript["improvement_history"][0]["score"] == pytest.approx(0.72)
    assert all(r["score_source"] == "ai_review" for r in transcript["improvement_history"])


@respx.mock
def test_locked_score_history_stops_without_using_ai_review_on_later_iter():
    _mount_common(respx)
    n = {"submit": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        n["submit"] += 1
        return httpx.Response(200, json={"id": f"sub-{n['submit']}"})

    respx.post(f"{API}/api/claims/claim-1/submit").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        if n["submit"] == 1:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "numeric_score": 0.70,
                            "reasoning": "sh1",
                            "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"items": []})

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)
    respx.get(url__regex=r".*/api/submissions/sub-\d+$").mock(
        return_value=httpx.Response(
            200, json={"ai_review": {"score": 99, "notes": "do not use", "issues": []}}
        )
    )

    def cb(reasoning, diagnostics, best_deliverable):
        return {"content": "v2", "format": "text"}

    h = _harness(mutation_callback=cb, max_iterations=3, poll_timeout=0.15)
    transcript = h.run()
    assert transcript["score_source"] == "score_history"
    assert len(transcript["improvement_history"]) == 1
    assert transcript["best_score"] == 0.70
    assert n["submit"] == 2


@respx.mock
def test_score_source_locked_score_history_ignores_ai_review():
    _mount_common(respx)
    n = {"submit": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        n["submit"] += 1
        return httpx.Response(200, json={"id": f"sub-{n['submit']}"})

    respx.post(f"{API}/api/claims/claim-1/submit").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        items = [
            {
                "numeric_score": 0.80,
                "reasoning": "sh",
                "diagnostics": {
                    "_submission_id": f"sub-{n['submit']}",
                    "actionable_gaps": [],
                },
            }
        ]
        return httpx.Response(200, json={"items": items})

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)
    respx.get(url__regex=r".*/api/submissions/sub-\d+$").mock(
        return_value=httpx.Response(
            200, json={"ai_review": {"score": 10, "notes": "wrong", "issues": []}}
        )
    )

    def cb(reasoning, diagnostics, best_deliverable):
        return MutationResult({"content": "v2", "format": "text"}, patched=False)

    h = _harness(mutation_callback=cb, max_iterations=2)
    transcript = h.run()
    assert transcript["score_source"] == "score_history"
    assert transcript["improvement_history"][0]["score"] == 0.80


@respx.mock
def test_patched_false_single_claim_and_submit():
    _mount_common(respx)
    claims = {"n": 0}
    submits = {"n": 0}

    def on_claim(request: httpx.Request) -> httpx.Response:
        claims["n"] += 1
        return httpx.Response(200, json={"id": f"claim-{claims['n']}"})

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(f"{API}/api/bounties/{BOUNTY}/claim").mock(side_effect=on_claim)
    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.5,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    def cb(reasoning, diagnostics, best_deliverable):
        return MutationResult(deliverable=best_deliverable, patched=False)

    h = _harness(mutation_callback=cb, max_iterations=3)
    transcript = h.run()
    assert claims["n"] == 1
    assert submits["n"] == 1
    assert len(transcript["improvement_history"]) == 1
    assert transcript["best_score"] == 0.5


@respx.mock
def test_patched_true_identical_identity_does_not_resubmit():
    _mount_common(respx)
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.4,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    def cb(reasoning, diagnostics, best_deliverable):
        return MutationResult(
            deliverable={"content": "v1", "format": "text"},
            patched=True,
            ops_applied=("align_counts",),
        )

    h = _harness(mutation_callback=cb, max_iterations=3)
    transcript = h.run()
    assert submits["n"] == 1
    assert len(transcript["improvement_history"]) == 1


@respx.mock
def test_second_submit_of_same_identity_halts():
    _mount_common(respx)
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.4,
                        "reasoning": "ok",
                        "diagnostics": {
                            "_submission_id": f"sub-{submits['n']}",
                            "actionable_gaps": [],
                        },
                    }
                ]
            },
        )

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)

    calls = {"n": 0}

    def cb(reasoning, diagnostics, best_deliverable):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "v2", "format": "text"}
        return {"content": "v2", "format": "text"}

    h = _harness(mutation_callback=cb, max_iterations=3)
    transcript = h.run()
    assert submits["n"] == 2
    assert len(transcript["improvement_history"]) == 2


@respx.mock
def test_custom_identity_ignores_provenance_hash():
    _mount_common(respx)
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.4,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    def ident(d: dict) -> str:
        return d.get("content", "")

    def cb(reasoning, diagnostics, best_deliverable):
        return {
            "content": "v1",
            "format": "text",
            "provenance": {"content_hash": "changed"},
        }

    initial = {"content": "v1", "format": "text", "provenance": {"content_hash": "old"}}
    h = _harness(
        mutation_callback=cb,
        initial_deliverable=initial,
        candidate_identity=ident,
        max_iterations=3,
    )
    transcript = h.run()
    assert submits["n"] == 1
    assert len(transcript["improvement_history"]) == 1


@respx.mock
def test_callback_inplace_mutation_does_not_change_saved_best():
    _mount_common(respx)
    saved = {}

    def on_submit(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "sub-1"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.4,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    def cb(reasoning, diagnostics, best_deliverable):
        best_deliverable["content"] = "mutated-in-place"
        saved["best"] = best_deliverable
        return MutationResult(deliverable=best_deliverable, patched=False)

    h = _harness(mutation_callback=cb, max_iterations=2)
    h.run()
    assert h._best_deliverable["content"] == "v1"


@respx.mock
def test_rejected_feedback_after_regression():
    _mount_common(respx)
    n = {"s": 0}
    seen = {}

    def on_submit(request: httpx.Request) -> httpx.Response:
        n["s"] += 1
        return httpx.Response(200, json={"id": f"sub-{n['s']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        score = 0.80 if n["s"] == 1 else 0.40
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": score,
                        "reasoning": f"r{n['s']}",
                        "diagnostics": {
                            "_submission_id": f"sub-{n['s']}",
                            "actionable_gaps": [f"gap-{n['s']}"],
                        },
                    }
                ]
            },
        )

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)

    def cb(reasoning, diagnostics, best_deliverable, rejected=None):
        seen["reasoning"] = reasoning
        seen["gaps"] = diagnostics.get("actionable_gaps")
        seen["rejected"] = rejected
        if rejected is None:
            return {"content": "v2", "format": "text"}
        return MutationResult(deliverable=best_deliverable, patched=False)

    h = _harness(mutation_callback=cb, max_iterations=3)
    transcript = h.run()
    assert seen["reasoning"] == "r1"
    assert seen["gaps"] == ["gap-1"]
    assert isinstance(seen["rejected"], RejectedFeedback)
    assert seen["rejected"].score == 0.40
    assert transcript["best_score"] == 0.80
    assert transcript["best_iteration"] == 1
    assert transcript["improvement_history"][1]["score"] == 0.40
    assert transcript["improvement_history"][1]["kept"] is False


def test_signature_adapter_ctx_and_typeerror_not_retried():
    calls = {"n": 0}

    def boom(ctx: MutationContext):
        calls["n"] += 1
        raise TypeError("real callback bug")

    h = _harness(mutation_callback=boom)
    with pytest.raises(TypeError, match="real callback bug"):
        h._invoke_mutation(
            MutationContext(
                reasoning="r",
                diagnostics={},
                best_deliverable={"content": "x"},
                rejected=None,
                candidate_seen=lambda _d: False,
            )
        )
    assert calls["n"] == 1


def test_default_identity_does_not_strip_timestamps():
    a = {"content": "x", "scan_timestamp": "2026-01-01"}
    b = {"content": "x"}
    assert default_candidate_identity(a) != default_candidate_identity(b)


@respx.mock
def test_bare_dict_callback_halts_on_repeat():
    _mount_common(respx)
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)

    def on_history(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.3,
                        "reasoning": "ok",
                        "diagnostics": {
                            "_submission_id": f"sub-{submits['n']}",
                            "actionable_gaps": [],
                        },
                    }
                ]
            },
        )

    respx.get(url__regex=r".*/api/score-history.*").mock(side_effect=on_history)

    def cb(reasoning, diagnostics, best_deliverable):
        return {"content": "same", "format": "text"}

    h = _harness(
        mutation_callback=cb,
        initial_deliverable={"content": "same", "format": "text"},
        max_iterations=3,
    )
    transcript = h.run()
    assert submits["n"] == 1
    assert len(transcript["improvement_history"]) == 1


@respx.mock
def test_submit_posts_recon_envelope_without_double_wrap():
    _mount_common(respx)
    bodies = []

    def on_submit(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "sub-1"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.9,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )
    payload = {
        "deliverable": {"content": "{}", "content_type": "application/json"},
        "provenance": {"source_type": "passive_recon"},
    }
    h = _harness(
        mutation_callback=lambda r, d, b: MutationResult(b, patched=False),
        initial_deliverable=payload,
        score_threshold=0.85,
        max_iterations=2,
    )
    h.run()
    assert bodies[0]["deliverable"]["content"] == "{}"
    assert bodies[0]["provenance"]["source_type"] == "passive_recon"
    assert "deliverable" not in bodies[0]["deliverable"]


@respx.mock
def test_on_submitted_fires_after_submit_success():
    _mount_common(respx)
    hooks = []
    submits = {"n": 0}

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.5,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    h = _harness(
        mutation_callback=lambda r, d, b: MutationResult(b, patched=False),
        on_submitted=lambda sid, iteration: hooks.append((sid, iteration)),
        max_iterations=2,
    )
    transcript = h.run()
    assert hooks == [("sub-1", 1)]
    assert transcript["last_submission_id"] == "sub-1"
    assert submits["n"] == 1


@respx.mock
def test_on_submitted_does_not_fire_when_submit_raises():
    _mount_common(respx)
    hooks = []
    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(
        return_value=httpx.Response(500, text="submit failed")
    )

    h = _harness(
        mutation_callback=lambda r, d, b: MutationResult(b, patched=False),
        on_submitted=lambda sid, iteration: hooks.append((sid, iteration)),
        max_iterations=2,
    )
    with pytest.raises(httpx.HTTPStatusError):
        h.run()
    assert hooks == []


@respx.mock
def test_on_submitted_raise_stops_without_second_claim():
    _mount_common(respx)
    claims = {"n": 0}
    submits = {"n": 0}

    def on_claim(request: httpx.Request) -> httpx.Response:
        claims["n"] += 1
        return httpx.Response(200, json={"id": f"claim-{claims['n']}"})

    def on_submit(request: httpx.Request) -> httpx.Response:
        submits["n"] += 1
        return httpx.Response(200, json={"id": f"sub-{submits['n']}"})

    respx.post(f"{API}/api/bounties/{BOUNTY}/claim").mock(side_effect=on_claim)
    respx.post(url__regex=r".*/api/claims/.*/submit$").mock(side_effect=on_submit)
    respx.get(url__regex=r".*/api/score-history.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "numeric_score": 0.5,
                        "reasoning": "ok",
                        "diagnostics": {"_submission_id": "sub-1", "actionable_gaps": []},
                    }
                ]
            },
        )
    )

    def boom(submission_id: str, iteration: int) -> None:
        raise RuntimeError(f"commit failed for {submission_id}")

    h = _harness(
        mutation_callback=lambda r, d, b: {"content": "v2", "format": "text"},
        on_submitted=boom,
        max_iterations=3,
    )
    transcript = h.run()
    assert submits["n"] == 1
    assert claims["n"] == 1
    assert transcript["last_submission_id"] == "sub-1"
    assert len(transcript["improvement_history"]) == 1
