"""SettleBridge Training Harness.

A thin, framework-agnostic orchestration loop that drives a registered agent
through repeated training iterations on a SettleBridge bounty.

The harness speaks only the SettleBridge REST API.  It has no access to the
agent's internals — no prompt slots, no model parameters, no framework SDKs.
The only lever it controls is the content of the next submission.

Boundary contract for ``mutation_callback``
------------------------------------------
The callback receives the feedback that scored the **best** deliverable (not
necessarily the latest), a deepcopy of that deliverable, and optional rejected
feedback for the just-scored loser.  It may return a ``MutationResult`` or a
bare ``dict`` (treated as ``patched=True``).  Unchanged or repeated candidates
still halt; ``patched=True`` is only a hint.

Supported signatures (bound once via ``inspect.signature``)::

    (reasoning, diagnostics, best_deliverable) -> dict | MutationResult
    (reasoning, diagnostics, best_deliverable, rejected=None) -> ...
    (ctx: MutationContext) -> dict | MutationResult
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT = 120.0
_RETRY_WAIT_MIN = 1.0
_RETRY_WAIT_MAX = 10.0
_RETRY_MAX_ATTEMPTS = 4

_EMA_LAMBDA = 0.1

_BOUNTY_CLOSED_MSG = "Bounty closed after prior-iteration acceptance"


class HarnessError(RuntimeError):
    """Raised when the harness encounters a non-retryable API error."""


class BudgetExhaustedError(HarnessError):
    """Raised when the stake budget would be exceeded before the next iteration."""


@dataclass(frozen=True)
class MutationResult:
    deliverable: dict
    patched: bool
    ops_applied: tuple[str, ...] = ()
    stop_reason: str = ""


@dataclass(frozen=True)
class RejectedFeedback:
    score: float
    reasoning: str
    diagnostics: dict
    submission_id: str = ""


@dataclass(frozen=True)
class MutationContext:
    reasoning: str
    diagnostics: dict
    best_deliverable: dict
    rejected: RejectedFeedback | None
    candidate_seen: Callable[[dict], bool]


MutationCallback = Callable[..., Any]
IterationCallback = Callable[[int, float], None]
SubmittedCallback = Callable[[str, int], None]
CandidateIdentity = Callable[[dict], str]


def default_candidate_identity(deliverable: dict) -> str:
    """Envelope identity: stable JSON hash of the whole payload. No key stripping."""
    blob = json.dumps(deliverable, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _coerce_mutation(raw: Any) -> MutationResult:
    if isinstance(raw, MutationResult):
        return raw
    if isinstance(raw, dict):
        return MutationResult(deliverable=raw, patched=True)
    raise TypeError(
        f"mutation_callback must return MutationResult or dict, got {type(raw)!r}"
    )


def _bind_mutation_callback(
    callback: MutationCallback,
) -> Callable[[MutationContext], MutationResult]:
    """Adapt a user callback once. Never probe arity by invoking the callback."""
    sig = inspect.signature(callback)
    params = [
        p
        for p in sig.parameters.values()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and p.name not in ("self", "cls")
    ]
    names = [p.name for p in params]

    is_ctx = False
    if len(params) == 1:
        ann = params[0].annotation
        if ann is MutationContext or (
            isinstance(ann, str) and ann.endswith("MutationContext")
        ):
            is_ctx = True
        elif params[0].name in ("ctx", "context"):
            is_ctx = True

    if is_ctx:

        def invoke_ctx(ctx: MutationContext) -> MutationResult:
            return _coerce_mutation(callback(ctx))

        return invoke_ctx

    positional = [
        p
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    extra = {p.name for p in params}

    def _values(ctx: MutationContext) -> tuple[list[Any], dict[str, Any]]:
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        if len(positional) >= 3:
            args = [ctx.reasoning, ctx.diagnostics, ctx.best_deliverable]
        else:
            if "reasoning" in extra:
                kwargs["reasoning"] = ctx.reasoning
            if "diagnostics" in extra:
                kwargs["diagnostics"] = ctx.diagnostics
            if "best_deliverable" in extra:
                kwargs["best_deliverable"] = ctx.best_deliverable
        if "rejected" in extra and (
            len(positional) < 4 or positional[3].name == "rejected"
        ):
            if len(positional) >= 4:
                args.append(ctx.rejected)
            else:
                kwargs["rejected"] = ctx.rejected
        if "candidate_seen" in extra:
            kwargs["candidate_seen"] = ctx.candidate_seen
        return args, kwargs

    def invoke_args(ctx: MutationContext) -> MutationResult:
        args, kwargs = _values(ctx)
        try:
            bound = sig.bind(*args, **kwargs)
        except TypeError as bind_exc:
            raise HarnessError(
                f"mutation_callback signature is not supported: {sig}"
            ) from bind_exc
        return _coerce_mutation(callback(*bound.args, **bound.kwargs))

    try:
        probe = MutationContext(
            reasoning="",
            diagnostics={},
            best_deliverable={},
            rejected=None,
            candidate_seen=lambda _d: False,
        )
        args, kwargs = _values(probe)
        sig.bind(*args, **kwargs)
    except TypeError as bind_exc:
        raise HarnessError(
            f"mutation_callback signature is not supported: {sig}"
        ) from bind_exc

    return invoke_args


def _submit_body(payload: dict) -> dict:
    """Use recon envelopes as-is; wrap a bare deliverable dict."""
    if isinstance(payload, dict) and "deliverable" in payload:
        return payload
    return {"deliverable": payload}


class TrainingHarness:
    """Orchestrate a self-improving agent training loop on SettleBridge."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        target_bounty_id: str,
        max_iterations: int,
        stake_budget: int,
        score_threshold: float,
        mutation_callback: MutationCallback,
        initial_deliverable: dict[str, Any],
        task_type: str | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
        versioning: bool = True,
        on_iteration: IterationCallback | None = None,
        on_submitted: SubmittedCallback | None = None,
        candidate_identity: CandidateIdentity | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.target_bounty_id = target_bounty_id
        self.max_iterations = max_iterations
        self.stake_budget = stake_budget
        self.score_threshold = score_threshold
        self.mutation_callback = mutation_callback
        self.initial_deliverable = initial_deliverable
        self.task_type = task_type
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.versioning = versioning
        self.on_iteration = on_iteration
        self.on_submitted = on_submitted
        self.candidate_identity: CandidateIdentity = (
            candidate_identity or default_candidate_identity
        )
        self._invoke_mutation = _bind_mutation_callback(mutation_callback)

        self._client: httpx.Client | None = None
        self.run_id: str | None = None
        self._stake_spent = 0

        self._best_deliverable: dict | None = None
        self._best_score: float = -1.0
        self._best_iteration: int = 0
        self._best_reasoning: str = ""
        self._best_diagnostics: dict = {}
        self._best_submission_id: str = ""
        self._improvement_history: list[dict] = []
        self._seen_identities: set[str] = set()
        self._score_source: str | None = None
        self._last_submission_id: str = ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, **params: Any) -> dict:
        assert self._client is not None
        resp = self._client.get(
            f"{self.api_url}{path}", params=params, headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict | None = None) -> dict:
        assert self._client is not None
        resp = self._client.post(
            f"{self.api_url}{path}", json=body, headers=self._headers()
        )
        if not resp.is_success:
            logger.error("POST %s → %d: %s", path, resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
        stop=stop_after_attempt(_RETRY_MAX_ATTEMPTS),
        reraise=True,
    )
    def _claim_bounty(self) -> str:
        body: dict[str, Any] = {}
        if self.run_id:
            body["training_run_id"] = self.run_id
        result = self._post(f"/api/bounties/{self.target_bounty_id}/claim", body=body or None)
        claim_id = result.get("id") or result.get("claim_id")
        if not claim_id:
            raise HarnessError(f"Claim response missing id: {result}")
        return claim_id

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
        stop=stop_after_attempt(_RETRY_MAX_ATTEMPTS),
        reraise=True,
    )
    def _submit(self, claim_id: str, payload: dict) -> str:
        """Submit the callback payload as the request body (no extra wrap)."""
        body = _submit_body(payload)
        try:
            result = self._post(f"/api/claims/{claim_id}/submit", body=body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and (
                "not in active status" in exc.response.text
                or "Claim is not" in exc.response.text
            ):
                logger.warning(
                    "Claim %s is no longer active. Re-claiming and retrying submit.",
                    claim_id,
                )
                try:
                    new_claim_id = self._claim_bounty()
                except (httpx.HTTPStatusError, HarnessError) as inner:
                    raise HarnessError(_BOUNTY_CLOSED_MSG) from inner
                result = self._post(
                    f"/api/claims/{new_claim_id}/submit", body=body
                )
            else:
                raise
        sub_id = result.get("id") or result.get("submission_id")
        if not sub_id:
            raise HarnessError(f"Submit response missing id: {result}")
        return str(sub_id)

    def _score_history_for(self, submission_id: str) -> dict | None:
        if not self.run_id:
            return None
        try:
            raw = self._get(
                "/api/score-history",
                training_run_id=self.run_id,
                limit=500,
            )
        except Exception:
            return None
        rows = raw if isinstance(raw, list) else raw.get("items", [])
        for row in rows:
            diag = row.get("diagnostics") or {}
            if str(diag.get("_submission_id") or "") == str(submission_id):
                return row
        return None

    def _ai_review_for(self, submission_id: str) -> dict | None:
        try:
            sub = self._get(f"/api/submissions/{submission_id}")
        except Exception:
            return None
        ai = sub.get("ai_review") or {}
        ai_score = ai.get("score")
        if ai_score is None:
            return None
        rec = ai.get("recommendation", "")
        numeric = float(ai_score) / 100.0
        return {
            "numeric_score": numeric,
            "reasoning": ai.get("notes", ""),
            "diagnostics": {
                "actionable_gaps": ai.get("issues", []),
                "recommendation": rec,
                "holdback_percent": ai.get("holdback_percent", 0),
                "_submission_id": str(submission_id),
            },
        }

    def _poll_for_score(self, submission_id: str) -> dict | None:
        """Wait for a verdict bound to ``submission_id`` from the locked source.

        First scored iteration prefers score-history when both are present and
        locks ``_score_source`` for the rest of the run. Later iterations poll
        only that source; a missing verdict times out without using the other.
        """
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            if self._score_source in (None, "score_history"):
                row = self._score_history_for(submission_id)
                if row is not None and self._score_source in (None, "score_history"):
                    if self._score_source is None:
                        self._score_source = "score_history"
                    if self._score_source == "score_history":
                        out = dict(row)
                        out["score_source"] = "score_history"
                        return out
            if self._score_source in (None, "ai_review"):
                # Prefer score-history on the first bind: if it appeared in
                # this same pass, the branch above already returned.
                row = self._ai_review_for(submission_id)
                if row is not None:
                    if self._score_source is None:
                        self._score_source = "ai_review"
                    if self._score_source == "ai_review":
                        out = dict(row)
                        out["score_source"] = "ai_review"
                        return out
            time.sleep(self.poll_interval)
        return None

    def _init_run(self) -> None:
        body = {
            "bounty_id": self.target_bounty_id,
            "max_iterations": self.max_iterations,
            "stake_budget": self.stake_budget,
            "score_threshold": self.score_threshold,
            "task_type": self.task_type,
        }
        try:
            result = self._post("/api/training/runs", body=body)
            self.run_id = result.get("run_id")
            if self.run_id:
                logger.info("Training run %s initialised", self.run_id)
            else:
                logger.warning("Training run init returned no run_id — continuing without one")
        except Exception as exc:
            logger.warning(
                "SettleBridge /api/training/runs not available (%s) — "
                "running without training-run tracking",
                exc,
            )
            self.run_id = None

    def _complete_run(self) -> dict:
        if not self.run_id:
            return {}
        try:
            result = self._post(f"/api/training/runs/{self.run_id}/complete")
            logger.info(
                "Training run %s complete: %d iterations, EMA=%.4f, merkle=%s",
                self.run_id,
                result.get("total_iterations", 0),
                result.get("final_training_ema", 0.0),
                result.get("merkle_root"),
            )
            return result
        except Exception as exc:
            logger.warning("Could not complete training run: %s", exc)
            return {}

    def _fetch_transcript(self) -> dict:
        if not self.run_id:
            return {}
        try:
            return self._get(f"/api/training/runs/{self.run_id}/transcript")
        except Exception as exc:
            logger.warning("Could not fetch training transcript: %s", exc)
            return {}

    def run(self) -> dict:
        self._best_deliverable = None
        self._best_score = -1.0
        self._best_iteration = 0
        self._best_reasoning = ""
        self._best_diagnostics = {}
        self._best_submission_id = ""
        self._improvement_history = []
        self._seen_identities = set()
        self._score_source = None
        self._last_submission_id = ""
        self._stake_spent = 0

        with httpx.Client(timeout=30.0) as client:
            self._client = client
            try:
                return self._run_loop()
            finally:
                self._client = None

    def _candidate_seen(self, deliverable: dict) -> bool:
        return self.candidate_identity(deliverable) in self._seen_identities

    def _run_loop(self) -> dict:
        self._init_run()

        deliverable = copy.deepcopy(self.initial_deliverable)
        pending_ops: tuple[str, ...] = ()

        for iteration in range(1, self.max_iterations + 1):
            logger.info(
                "Iteration %d / %d (stake_spent=%d / %d ATE)",
                iteration,
                self.max_iterations,
                self._stake_spent,
                self.stake_budget,
            )

            if self._stake_spent >= self.stake_budget:
                raise BudgetExhaustedError(
                    f"Stake budget {self.stake_budget} ATE exhausted after "
                    f"{iteration - 1} iterations"
                )

            claim_id = self._claim_bounty()
            logger.info("Claimed bounty %s → claim %s", self.target_bounty_id, claim_id)

            try:
                sub_id = self._submit(claim_id, deliverable)
            except HarnessError as exc:
                if _BOUNTY_CLOSED_MSG in str(exc):
                    logger.info(
                        "Bounty was accepted/closed during iteration %d — stopping.",
                        iteration,
                    )
                    break
                raise
            logger.info("Submitted deliverable for claim %s → sub %s", claim_id, sub_id)
            self._last_submission_id = sub_id

            hook_failed = False
            if self.on_submitted:
                try:
                    self.on_submitted(sub_id, iteration)
                except Exception:
                    hook_failed = True
                    logger.exception(
                        "on_submitted failed after successful submit %s — "
                        "stopping without retrying submit",
                        sub_id,
                    )

            score_row = self._poll_for_score(sub_id)
            if score_row is None:
                logger.warning(
                    "Timed out waiting for locked score source (%s) on iteration %d. Stopping.",
                    self._score_source,
                    iteration,
                )
                break

            last_score = float(score_row.get("numeric_score", 0.0))
            last_reasoning = score_row.get("reasoning") or ""
            last_diagnostics = dict(score_row.get("diagnostics") or {})
            last_diagnostics["numeric_score"] = last_score
            last_diagnostics["_submission_id"] = sub_id
            last_diagnostics["_claim_id"] = claim_id
            score_source = score_row.get("score_source") or self._score_source

            identity = self.candidate_identity(deliverable)
            self._seen_identities.add(identity)

            logger.info(
                "Iteration %d score: %.4f  source=%s  gaps: %s",
                iteration,
                last_score,
                score_source,
                last_diagnostics.get("actionable_gaps", [])[:3],
            )

            if self.run_id:
                try:
                    run_status = self._get(f"/api/training/runs/{self.run_id}")
                    self._stake_spent = run_status.get("stake_spent", self._stake_spent)
                except Exception:
                    pass

            rejected: RejectedFeedback | None = None
            if self.versioning:
                kept = last_score > self._best_score
                if kept:
                    self._best_score = last_score
                    self._best_deliverable = copy.deepcopy(deliverable)
                    self._best_iteration = iteration
                    self._best_reasoning = last_reasoning
                    self._best_diagnostics = copy.deepcopy(last_diagnostics)
                    self._best_submission_id = sub_id
                    logger.info(
                        "Iteration %d kept as new best (score=%.4f)",
                        iteration,
                        last_score,
                    )
                else:
                    rejected = RejectedFeedback(
                        score=last_score,
                        reasoning=last_reasoning,
                        diagnostics=copy.deepcopy(last_diagnostics),
                        submission_id=sub_id,
                    )
                    logger.info(
                        "Iteration %d reverted (score=%.4f < best=%.4f)",
                        iteration,
                        last_score,
                        self._best_score,
                    )
            else:
                self._best_deliverable = copy.deepcopy(deliverable)
                self._best_score = max(self._best_score, last_score)
                self._best_iteration = iteration
                self._best_reasoning = last_reasoning
                self._best_diagnostics = copy.deepcopy(last_diagnostics)
                self._best_submission_id = sub_id
                kept = True

            history_row: dict[str, Any] = {
                "iteration_index": iteration,
                "score": last_score,
                "kept": kept,
                "cumulative_best": self._best_score,
                "reasoning": last_reasoning,
                "score_source": score_source,
                "submission_id": sub_id,
            }
            if pending_ops:
                history_row["ops_applied"] = list(pending_ops)
            self._improvement_history.append(history_row)
            pending_ops = ()

            if self.on_iteration:
                try:
                    self.on_iteration(iteration, last_score)
                except Exception:
                    logger.exception("on_iteration callback failed")

            if hook_failed:
                logger.info(
                    "on_submitted failed — stopping before next mutation "
                    "(submission_id=%s)",
                    sub_id,
                )
                break

            if last_score >= self.score_threshold:
                logger.info(
                    "Score threshold %.4f reached (score=%.4f). Stopping.",
                    self.score_threshold,
                    last_score,
                )
                break

            if iteration >= self.max_iterations:
                break

            if self._best_deliverable is None:
                break

            ctx = MutationContext(
                reasoning=self._best_reasoning,
                diagnostics=copy.deepcopy(self._best_diagnostics),
                best_deliverable=copy.deepcopy(self._best_deliverable),
                rejected=rejected,
                candidate_seen=self._candidate_seen,
            )
            result = self._invoke_mutation(ctx)
            baseline_id = self.candidate_identity(self._best_deliverable)
            new_id = self.candidate_identity(result.deliverable)
            if new_id == baseline_id or new_id in self._seen_identities:
                result = MutationResult(
                    deliverable=result.deliverable,
                    patched=False,
                    ops_applied=result.ops_applied,
                    stop_reason=result.stop_reason,
                )
            if not result.patched:
                logger.info(
                    "Mutation produced no new candidate — stopping before next claim. "
                    "reason=%s",
                    result.stop_reason or "unpatched",
                )
                break
            deliverable = result.deliverable
            pending_ops = result.ops_applied

        self._complete_run()
        transcript = self._fetch_transcript()
        transcript["best_score"] = self._best_score
        transcript["best_iteration"] = self._best_iteration
        transcript["improvement_history"] = self._improvement_history
        transcript["score_source"] = self._score_source
        transcript["last_submission_id"] = self._last_submission_id
        return transcript

    def plot(self, format: str = "html") -> "str | bytes":
        """Generate a score trajectory visualisation from the completed run."""
        if not self._improvement_history:
            raise RuntimeError("No improvement history — call run() before plot().")
        if format not in ("html", "png"):
            raise ValueError(f"format must be 'html' or 'png', got {format!r}")

        iterations = [h["iteration_index"] for h in self._improvement_history]
        scores = [h["score"] for h in self._improvement_history]
        kept_flags = [h["kept"] for h in self._improvement_history]
        reasonings = [h.get("reasoning", "") for h in self._improvement_history]

        ema_values: list[float] = []
        ema = scores[0]
        for s in scores:
            ema = _EMA_LAMBDA * s + (1 - _EMA_LAMBDA) * ema
            ema_values.append(ema)

        if format == "html":
            try:
                import plotly.graph_objects as go
            except ImportError:
                raise ImportError(
                    "Plotly is not installed. "
                    "Run: pip install 'settlebridge-harness[viz]'"
                )

            keep_x = [iterations[i] for i, k in enumerate(kept_flags) if k]
            keep_y = [scores[i] for i, k in enumerate(kept_flags) if k]
            keep_hover = [reasonings[i] for i, k in enumerate(kept_flags) if k]
            revert_x = [iterations[i] for i, k in enumerate(kept_flags) if not k]
            revert_y = [scores[i] for i, k in enumerate(kept_flags) if not k]
            revert_hover = [reasonings[i] for i, k in enumerate(kept_flags) if not k]

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=keep_x,
                    y=keep_y,
                    mode="markers",
                    marker=dict(color="#2ecc71", size=10, symbol="circle"),
                    name="Keep",
                    hovertext=keep_hover,
                    hovertemplate="Iter %{x}<br>Score: %{y:.4f}<br>%{hovertext}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=revert_x,
                    y=revert_y,
                    mode="markers",
                    marker=dict(color="#e74c3c", size=10, symbol="x"),
                    name="Revert",
                    hovertext=revert_hover,
                    hovertemplate="Iter %{x}<br>Score: %{y:.4f}<br>%{hovertext}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=iterations,
                    y=scores,
                    mode="lines",
                    line=dict(color="#95a5a6", width=1, dash="dot"),
                    name="Raw score",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=iterations,
                    y=ema_values,
                    mode="lines",
                    line=dict(color="#3498db", width=2),
                    name=f"EMA (λ={_EMA_LAMBDA})",
                )
            )
            fig.add_hline(
                y=self.score_threshold,
                line_dash="dash",
                line_color="#f39c12",
                annotation_text=f"Threshold {self.score_threshold}",
                annotation_position="top right",
            )
            fig.update_layout(
                title="SettleBridge Training Trajectory",
                xaxis_title="Iteration",
                yaxis_title="Score",
                yaxis=dict(range=[0, 1.05]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                hovermode="x unified",
            )
            return fig.to_html(full_html=True, include_plotlyjs="cdn")

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError(
                "Matplotlib is not installed. "
                "Run: pip install 'settlebridge-harness[viz]'"
            )

        from io import BytesIO

        fig, ax = plt.subplots(figsize=(10, 5))
        keep_x = [iterations[i] for i, k in enumerate(kept_flags) if k]
        keep_y = [scores[i] for i, k in enumerate(kept_flags) if k]
        revert_x = [iterations[i] for i, k in enumerate(kept_flags) if not k]
        revert_y = [scores[i] for i, k in enumerate(kept_flags) if not k]
        ax.plot(
            iterations,
            scores,
            color="#95a5a6",
            linewidth=1,
            linestyle="dotted",
            label="Raw score",
        )
        ax.plot(
            iterations,
            ema_values,
            color="#3498db",
            linewidth=2,
            label=f"EMA (λ={_EMA_LAMBDA})",
        )
        ax.axhline(
            y=self.score_threshold,
            color="#f39c12",
            linestyle="--",
            label=f"Threshold {self.score_threshold}",
        )
        if keep_x:
            ax.scatter(keep_x, keep_y, color="#2ecc71", s=80, zorder=5, label="Keep")
        if revert_x:
            ax.scatter(
                revert_x,
                revert_y,
                color="#e74c3c",
                s=80,
                marker="x",
                zorder=5,
                label="Revert",
            )
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Score")
        ax.set_ylim(0, 1.05)
        ax.set_title("SettleBridge Training Trajectory")
        ax.legend(loc="lower right")
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
