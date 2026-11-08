"""Verifier world for django-truncator-html-redos (denial_of_service).

The property: ``django.utils.text.Truncator`` ``chars()`` and ``words()`` with
``html=True`` finish in time roughly linear in the input length. CVE-2023-43665
capped HTML input length. CVE-2024-27351 says that cap left ``words()`` able to
backtrack. The completing fix scans words without that regex. ``chars`` and
``words`` run those methods in a forked child and record ``cpu_seconds`` when
the work exceeds the contract threshold. A child killed for exceeding its
timeout counts as exceeding. An empty truncation suffix is passed so the call
does not load translation catalogs.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_TEXT_BYTES = 64 * 1024
MAX_NUM = 1_000_000
CHILD_TIMEOUT = 8.0


def _truncate(text: str, num: int, by_words: bool):
    from django.utils.text import Truncator

    tool = Truncator(text)
    if by_words:
        result = tool.words(num, truncate="", html=True)
    else:
        result = tool.chars(num, truncate="", html=True)
    return {"text": result}


class World(BaseWorld):
    def chars(self, text: str, num: int, threshold_seconds: float = 2.0):
        """Truncate ``text`` by characters and record a CPU overrun.

        ``html=True`` is always used. Returns the child Outcome.
        ``cpu_seconds``, ``wall_seconds``, and ``killed`` come from that
        child. ``outcome.exceeded`` is True when CPU time, or a timeout kill,
        passed ``threshold_seconds`` — the same condition that writes the
        resource capture. ``outcome.value`` is ``{"text": truncated}``.
        """
        return self._measure(text, num, threshold_seconds, by_words=False)

    def words(self, text: str, num: int, threshold_seconds: float = 2.0):
        """Truncate ``text`` by words and record a CPU overrun.

        ``html=True`` is always used. Returns the child Outcome.
        ``cpu_seconds``, ``wall_seconds``, and ``killed`` come from that
        child. ``outcome.exceeded`` is True when CPU time, or a timeout kill,
        passed ``threshold_seconds`` — the same condition that writes the
        resource capture. ``outcome.value`` is ``{"text": truncated}``.
        """
        return self._measure(text, num, threshold_seconds, by_words=True)

    def _measure(self, text: str, num: int, threshold_seconds: float, by_words: bool):
        body = require_text(text, "text", MAX_TEXT_BYTES)
        if type(num) is not int:
            raise TypeError("num must be a plain int")
        if num < 0 or num > MAX_NUM:
            raise ValueError(f"num must be between 0 and {MAX_NUM}")
        if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, (int, float)):
            raise TypeError("threshold_seconds must be a real number")
        threshold = float(threshold_seconds)
        outcome = self.call(_truncate, body, num, by_words, timeout=CHILD_TIMEOUT)
        cpu = outcome.cpu_seconds
        if outcome.killed and cpu <= threshold:
            cpu = threshold + 1.0
        outcome.exceeded = cpu > threshold
        if outcome.exceeded:
            capture.resource("cpu_seconds", cpu, threshold)
        return outcome
