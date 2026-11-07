"""Platform-operated challenger worker (owner: P1-4).

Sees only the frozen bundle: the contract, the source tarball, and the diff. It never
sees verifier fixtures, observers, canaries, or other participants' narratives. It
produces structured claim drafts and test files under ``challenge_tests/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
    raise NotImplementedError
