"""SettleBridge Training Harness.

A thin, framework-agnostic orchestration loop that drives a registered agent
through repeated training iterations on a SettleBridge bounty.

The harness speaks only the SettleBridge REST API.  It has no access to the
agent's internals — no prompt slots, no model parameters, no framework SDKs.
The only lever it controls is the content of the ``deliverable`` dict sent
to ``POST /api/claims/{id}/submit`` on each iteration.

Boundary contract for ``mutation_callback``
------------------------------------------
The callback receives the Mediator's reasoning, structured diagnostic, and the
best-scoring deliverable seen so far.  It MUST return a ``dict`` that will be
used verbatim as the ``deliverable`` field of the next submission request body.

Signature::

    def my_callback(reasoning: str, diagnostics: dict, best_deliverable: dict) -> dict:
        # reasoning        — plain-text explanation from the Mediator
        # diagnostics      — {"task_type": ..., "actionable_gaps": [...], "details": {...}}
        # best_deliverable — the deliverable that produced the highest score so far
        # Return the next submission's deliverable payload.
        return {"content": "...updated output...", "format": "text"}

When ``versioning=True`` (default), ``best_deliverable`` is the deliverable
from the highest-scoring iteration, not the most recent one.  Callbacks should
use it as the starting point for the next mutation rather than the last
iteration's deliverable, which may have regressed.

When ``versioning=False`` (legacy), ``best_deliverable`` equals the most recent
deliverable, preserving the original behaviour.

Budget enforcement (v1 limitation)
-----------------------------------
The stake budget ceiling is enforced client-side by the harness.  The server
does not block a claim when ``stake_spent >= stake_budget``.  A misbehaving or
modified harness could overdraw the intended budget.  This is acceptable in v1
because the operator is both requester and provider; all ATE is real, so cost
is a natural deterrent.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# (reasoning, diagnostics, best_deliverable) -> next deliverable
MutationCallback = Callable[[str, dict, dict], dict]

_DEFAULT_POLL_INTERVAL = 5.0   # seconds between score-history polls
_DEFAULT_POLL_TIMEOUT  = 120.0 # seconds before giving up on a score appearing
_RETRY_WAIT_MIN        = 1.0
_RETRY_WAIT_MAX        = 10.0
_RETRY_MAX_ATTEMPTS    = 4

_EMA_LAMBDA = 0.1  # matches SettleBridge server-side EMA λ


class HarnessError(RuntimeError):
    """Raised when the harness encounters a non-retryable API error."""


class BudgetExhaustedError(HarnessError):
    """Raised when the stake budget would be exceeded before the next iteration."""


class TrainingHarness:
    """Orchestrate a self-improving agent training loop on SettleBridge.

    Args:
        api_url:           Base URL of the SettleBridge API (no trailing slash).
        api_key:           Bearer token for the agent operator's account.
        target_bounty_id:  UUID of the bounty to train against.
        max_iterations:    Hard cap on the number of training iterations.
        stake_budget:      Total ATE micro-stake budget across all iterations.
        score_threshold:   Stop early when ``numeric_score >= score_threshold``.
        mutation_callback: Callable ``(reasoning, diagnostics, best_deliverable)``
                           that returns the next submission's deliverable dict.
        initial_deliverable: The deliverable dict for the very first iteration.
        task_type:         Optional task type string forwarded to the Mediator.
        poll_interval:     Seconds between score-history polls (default 5 s).
        poll_timeout:      Seconds before giving up waiting for a score (default 120 s).
        versioning:        When True (default), keep/revert logic is active —
                           mutation always starts from the best-scoring
                           deliverable, not the most recent one.  Set to False
                           to restore the original always-mutate-from-latest
                           behaviour.
    """

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

        self._client: httpx.Client | None = None
        self.run_id: str | None = None
        self._stake_spent = 0

        # Best-so-far tracking (populated during run())
        self._best_deliverable: dict | None = None
        self._best_score: float = -1.0
        self._best_iteration: int = 0
        self._improvement_history: list[dict] = []

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _get(self, path: str, **params: Any) -> dict:
        assert self._client is not None
        resp = self._client.get(f"{self.api_url}{path}", params=params, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict | None = None) -> dict:
        assert self._client is not None
        resp = self._client.post(
            f"{self.api_url}{path}", json=body, headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Retryable API calls
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
        stop=stop_after_attempt(_RETRY_MAX_ATTEMPTS),
        reraise=True,
    )
    def _claim_bounty(self) -> str:
        """Claim the training bounty and return the claim_id."""
        result = self._post(f"/api/bounties/{self.target_bounty_id}/claim")
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
    def _submit(self, claim_id: str, deliverable: dict) -> str:
        """Submit deliverable for a claim and return the submission_id."""
        result = self._post(
            f"/api/claims/{claim_id}/submit",
            body={"deliverable": deliverable},
        )
        sub_id = result.get("id") or result.get("submission_id")
        if not sub_id:
            raise HarnessError(f"Submit response missing id: {result}")
        return sub_id

    def _poll_for_score(self) -> dict | None:
        """Poll /api/score-history until a new row appears for this run.

        Returns the latest score row dict, or None if the timeout expires.
        """
        deadline = time.monotonic() + self.poll_timeout
        last_count = 0
        while time.monotonic() < deadline:
            raw = self._get(
                "/api/score-history",
                training_run_id=self.run_id,
                limit=500,
            )
            # Support both envelope {"items": [...]} and bare list responses
            rows = raw if isinstance(raw, list) else raw.get("items", [])
            if len(rows) > last_count:
                return rows[-1]  # most recent row
            time.sleep(self.poll_interval)
        return None

    # ------------------------------------------------------------------
    # Training run lifecycle
    # ------------------------------------------------------------------

    def _init_run(self) -> None:
        """Create the training run on the server and store run_id."""
        body = {
            "bounty_id": self.target_bounty_id,
            "max_iterations": self.max_iterations,
            "stake_budget": self.stake_budget,
            "score_threshold": self.score_threshold,
            "task_type": self.task_type,
        }
        result = self._post("/api/training/runs", body=body)
        self.run_id = result.get("run_id")
        if not self.run_id:
            raise HarnessError(f"Training run init response missing run_id: {result}")
        logger.info("Training run %s initialised", self.run_id)

    def _complete_run(self) -> dict:
        """Trigger transcript generation and return the summary."""
        result = self._post(f"/api/training/runs/{self.run_id}/complete")
        logger.info(
            "Training run %s complete: %d iterations, EMA=%.4f, merkle=%s",
            self.run_id,
            result.get("total_iterations", 0),
            result.get("final_training_ema", 0.0),
            result.get("merkle_root"),
        )
        return result

    def _fetch_transcript(self) -> dict:
        return self._get(f"/api/training/runs/{self.run_id}/transcript")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Execute the training loop and return the final transcript.

        Loop invariant::

            for each iteration:
                1. Check budget ceiling (client-side, v1 limitation — see module docstring)
                2. Claim the bounty (creates a new micro-escrow on the exchange)
                3. Submit the current deliverable
                4. Poll score-history until the Mediator verdict appears
                5. keep/revert: update best_deliverable if score improved
                6. If score >= threshold → stop (success)
                7. Call mutation_callback(reasoning, diagnostics, best_deliverable)
            After loop: complete run → fetch and return transcript

        Returns:
            The transcript dict from ``GET /api/training/runs/{id}/transcript``,
            augmented with client-side fields: ``best_score``, ``best_iteration``,
            and ``improvement_history``.

        Raises:
            BudgetExhaustedError: If the remaining stake budget is too small to
                                   proceed before starting a new iteration.
            HarnessError: On non-retryable API failures.
        """
        # Reset per-run state so the harness is reusable
        self._best_deliverable = None
        self._best_score = -1.0
        self._best_iteration = 0
        self._improvement_history = []

        with httpx.Client(timeout=30.0) as client:
            self._client = client
            try:
                return self._run_loop()
            finally:
                self._client = None

    def _run_loop(self) -> dict:
        self._init_run()

        deliverable = self.initial_deliverable
        last_score: float | None = None
        last_reasoning: str = ""
        last_diagnostics: dict = {}

        for iteration in range(1, self.max_iterations + 1):
            logger.info("Iteration %d / %d (stake_spent=%d / %d ATE)",
                        iteration, self.max_iterations, self._stake_spent, self.stake_budget)

            # Client-side budget guard (v1 limitation: server does not enforce this)
            if self._stake_spent >= self.stake_budget:
                logger.warning(
                    "Stake budget exhausted after %d iterations "
                    "(spent=%d, budget=%d). Stopping.",
                    iteration - 1, self._stake_spent, self.stake_budget,
                )
                raise BudgetExhaustedError(
                    f"Stake budget {self.stake_budget} ATE exhausted after "
                    f"{iteration - 1} iterations"
                )

            # 1. Claim
            claim_id = self._claim_bounty()
            logger.info("Claimed bounty %s → claim %s", self.target_bounty_id, claim_id)

            # 2. Submit
            self._submit(claim_id, deliverable)
            logger.info("Submitted deliverable for claim %s", claim_id)

            # 3. Poll for Mediator score
            score_row = self._poll_for_score()
            if score_row is None:
                logger.warning(
                    "Timed out waiting for score on iteration %d. Stopping.", iteration
                )
                break

            last_score = score_row.get("numeric_score", 0.0)
            last_reasoning = score_row.get("reasoning") or ""
            last_diagnostics = score_row.get("diagnostics") or {}

            logger.info(
                "Iteration %d score: %.4f  gaps: %s",
                iteration,
                last_score,
                last_diagnostics.get("actionable_gaps", [])[:3],
            )

            # Update local stake tracker from the run status
            try:
                run_status = self._get(f"/api/training/runs/{self.run_id}")
                self._stake_spent = run_status.get("stake_spent", self._stake_spent)
            except Exception:
                pass  # non-fatal; client-side counter is the backup

            # 4. Keep/revert: update best-so-far state
            if self.versioning:
                kept = last_score > self._best_score
                if kept:
                    self._best_score = last_score
                    self._best_deliverable = deliverable
                    self._best_iteration = iteration
                    logger.info(
                        "Iteration %d kept as new best (score=%.4f)", iteration, last_score
                    )
                else:
                    logger.info(
                        "Iteration %d reverted (score=%.4f < best=%.4f)",
                        iteration, last_score, self._best_score,
                    )
            else:
                # Legacy mode: always advance from most recent deliverable
                self._best_deliverable = deliverable
                self._best_score = max(self._best_score, last_score)
                kept = True

            self._improvement_history.append({
                "iteration_index": iteration,
                "score": last_score,
                "kept": kept,
                "cumulative_best": self._best_score,
                "reasoning": last_reasoning,
            })

            # 5. Check threshold
            if last_score is not None and last_score >= self.score_threshold:
                logger.info(
                    "Score threshold %.4f reached (score=%.4f). Stopping.",
                    self.score_threshold, last_score,
                )
                break

            # 6. Mutate for next iteration (skip on last iteration)
            if iteration < self.max_iterations:
                deliverable = self.mutation_callback(
                    last_reasoning,
                    last_diagnostics,
                    self._best_deliverable,
                )

        # Complete the run, fetch transcript, and augment with client-side fields
        self._complete_run()
        transcript = self._fetch_transcript()
        transcript["best_score"] = self._best_score
        transcript["best_iteration"] = self._best_iteration
        transcript["improvement_history"] = self._improvement_history
        return transcript

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def plot(self, format: str = "html") -> "str | bytes":
        """Generate a score trajectory visualisation from the completed run.

        Must be called after ``run()``.  Uses ``improvement_history`` built
        during the run to render two series: raw score per iteration and a
        running EMA line matching the SettleBridge server λ=0.1.

        Args:
            format: ``"html"`` (default) returns a self-contained Plotly HTML
                    string.  ``"png"`` returns PNG bytes via matplotlib.

        Returns:
            ``str`` for ``format="html"``, ``bytes`` for ``format="png"``.

        Raises:
            RuntimeError: If called before ``run()`` (no history available).
            ImportError:  If the required viz library is not installed.
                          Install with ``pip install 'settlebridge-harness[viz]'``.
            ValueError:   If ``format`` is not ``"html"`` or ``"png"``.
        """
        if not self._improvement_history:
            raise RuntimeError(
                "No improvement history — call run() before plot()."
            )
        if format not in ("html", "png"):
            raise ValueError(f"format must be 'html' or 'png', got {format!r}")

        iterations = [h["iteration_index"] for h in self._improvement_history]
        scores = [h["score"] for h in self._improvement_history]
        kept_flags = [h["kept"] for h in self._improvement_history]
        reasonings = [h.get("reasoning", "") for h in self._improvement_history]

        # Running EMA (λ=0.1, same as SettleBridge server)
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

            # Keep markers
            fig.add_trace(go.Scatter(
                x=keep_x, y=keep_y,
                mode="markers",
                marker=dict(color="#2ecc71", size=10, symbol="circle"),
                name="Keep",
                hovertext=keep_hover,
                hovertemplate="Iter %{x}<br>Score: %{y:.4f}<br>%{hovertext}<extra></extra>",
            ))

            # Revert markers
            fig.add_trace(go.Scatter(
                x=revert_x, y=revert_y,
                mode="markers",
                marker=dict(color="#e74c3c", size=10, symbol="x"),
                name="Revert",
                hovertext=revert_hover,
                hovertemplate="Iter %{x}<br>Score: %{y:.4f}<br>%{hovertext}<extra></extra>",
            ))

            # Raw score line (behind markers)
            fig.add_trace(go.Scatter(
                x=iterations, y=scores,
                mode="lines",
                line=dict(color="#95a5a6", width=1, dash="dot"),
                name="Raw score",
                showlegend=True,
            ))

            # EMA line
            fig.add_trace(go.Scatter(
                x=iterations, y=ema_values,
                mode="lines",
                line=dict(color="#3498db", width=2),
                name=f"EMA (λ={_EMA_LAMBDA})",
            ))

            # Threshold line
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

        else:  # format == "png"
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

            ax.plot(iterations, scores, color="#95a5a6", linewidth=1,
                    linestyle="dotted", label="Raw score")
            ax.plot(iterations, ema_values, color="#3498db", linewidth=2,
                    label=f"EMA (λ={_EMA_LAMBDA})")
            ax.axhline(y=self.score_threshold, color="#f39c12", linestyle="--",
                       label=f"Threshold {self.score_threshold}")

            if keep_x:
                ax.scatter(keep_x, keep_y, color="#2ecc71", s=80,
                           zorder=5, label="Keep")
            if revert_x:
                ax.scatter(revert_x, revert_y, color="#e74c3c", s=80,
                           marker="x", zorder=5, label="Revert")

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
