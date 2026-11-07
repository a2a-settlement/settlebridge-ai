"""Verifier-owned observers (owner: P1-V). Frozen with the contract; the challenger
can neither see nor edit them.

Each observer reads captured runtime output and returns ObserverEvent values. The
authorization observer fires only when a seeded tenant-B canary token appears in a
response, log, or side channel available to the tenant-A identity.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from challenge_runner.protocol import ObserverEvent


class Observer(Protocol):
    name: str

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]: ...


def get_observer(name: str) -> Observer:
    raise NotImplementedError
