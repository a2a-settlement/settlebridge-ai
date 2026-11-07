"""Platform-operated challenger worker (owner: P1-4).

Sees only the frozen bundle: the contract, the source tarball, and the diff. It never
sees verifier fixtures, observers, canaries, or other participants' narratives. It
produces structured claim drafts and test files under ``challenge_tests/``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from challenge_runner.challenger.limits import MAX_COMPLETION_TOKENS
from challenge_runner.challenger.output import (
    model_tokens_from_client,
    package_bundle,
    persist_outputs,
    prepare_output,
    store_test_files,
)
from challenge_runner.challenger.parse import empty_rejection, merge_rejections, parse_model_output
from challenge_runner.challenger.prompt import build_prompts


@dataclass(frozen=True)
class ChallengeBundle:
    contract: dict
    source_dir: Path
    diff_text: str


@dataclass(frozen=True)
class ClaimDraft:
    """Same fields as backend ClaimIn; serialized with dataclasses.asdict."""

    failure_class: str
    scenario: dict
    oracle_citation: dict
    expected: str
    observed: str
    claimed_impact: str


class ModelClient(Protocol):
    """Injected by the caller so tests never reach a live model API."""

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str: ...


def run_challenger(
    bundle: ChallengeBundle, *, out_dir: Path, model_client: ModelClient, max_claims: int = 5
) -> list[ClaimDraft]:
    if isinstance(max_claims, bool) or not isinstance(max_claims, int) or max_claims < 0:
        raise ValueError("max_claims must be a non-negative integer")
    root = prepare_output(out_dir)
    system, prompt = build_prompts(
        contract=bundle.contract,
        source_dir=bundle.source_dir,
        diff_text=bundle.diff_text,
        max_claims=max_claims,
    )
    try:
        raw = model_client.complete(system=system, prompt=prompt, max_tokens=MAX_COMPLETION_TOKENS)
    except Exception:
        persist_outputs(
            root,
            claims=[],
            rejected=empty_rejection(),
            calls=1,
            model_tokens=model_tokens_from_client(model_client),
        )
        raise
    accepted, rejected = parse_model_output(raw, max_claims=max_claims)
    stored, write_rejected = store_test_files(root, accepted)
    rejected = merge_rejections(rejected, write_rejected)
    claims = [
        ClaimDraft(
            failure_class=item["failure_class"],
            scenario=item["scenario"],
            oracle_citation=item["oracle_citation"],
            expected=item["expected"],
            observed=item["observed"],
            claimed_impact=item["claimed_impact"],
        )
        for item in stored
    ]
    persist_outputs(
        root,
        claims=[asdict(claim) for claim in claims],
        rejected=rejected,
        calls=1,
        model_tokens=model_tokens_from_client(model_client),
    )
    package_bundle(root)
    return claims
