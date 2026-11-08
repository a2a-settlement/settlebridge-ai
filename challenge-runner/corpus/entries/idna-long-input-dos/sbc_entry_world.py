"""Verifier world for idna-long-input-dos (denial_of_service).

The property: ``idna.encode`` and ``idna.decode`` either reject a very long
input or finish in bounded time. Per-codepoint contextual checks walk the
whole label, so an uncapped label is quadratic in its length. The completed
repair rejects an oversize domain before that walk. ``encode`` and ``decode``
each run one library call in a forked child and record ``cpu_seconds`` when
that work exceeds the contract threshold. A child killed for exceeding its
timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_DOMAIN_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0


def _encode(domain: str):
    import idna

    return idna.encode(domain).decode("ascii")


def _decode(domain: str):
    import idna

    return idna.decode(domain)


def _threshold(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("threshold_seconds must be a real number")
    return float(value)


def _mark(outcome, threshold: float):
    cpu = outcome.cpu_seconds
    if outcome.killed and cpu <= threshold:
        cpu = threshold + 1.0
    outcome.exceeded = cpu > threshold
    if outcome.exceeded:
        capture.resource("cpu_seconds", cpu, threshold)
    return outcome


def _run(world: BaseWorld, fn, domain: str, threshold_seconds: float):
    text = require_text(domain, "domain", MAX_DOMAIN_BYTES)
    threshold = _threshold(threshold_seconds)
    outcome = world.call(fn, text, timeout=CHILD_TIMEOUT)
    return _mark(outcome, threshold)


class World(BaseWorld):
    def encode(self, domain: str, threshold_seconds: float = 2.0):
        """Encode ``domain`` and record a CPU overrun past ``threshold_seconds``.

        Calls ``idna.encode`` with the library defaults. Returns the child
        Outcome. ``cpu_seconds``, ``wall_seconds``, and ``killed`` come from
        that child. On success ``value`` is the ASCII domain. ``exceeded`` is
        True when CPU time, or a timeout kill, passed the threshold — the same
        condition that writes the resource capture.
        """
        return _run(self, _encode, domain, threshold_seconds)

    def decode(self, domain: str, threshold_seconds: float = 2.0):
        """Decode ``domain`` and record a CPU overrun past ``threshold_seconds``.

        Calls ``idna.decode`` with the library defaults. Returns the child
        Outcome. ``cpu_seconds``, ``wall_seconds``, and ``killed`` come from
        that child. On success ``value`` is the Unicode domain. ``exceeded`` is
        True when CPU time, or a timeout kill, passed the threshold — the same
        condition that writes the resource capture.
        """
        return _run(self, _decode, domain, threshold_seconds)
