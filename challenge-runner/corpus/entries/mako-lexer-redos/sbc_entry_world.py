"""Verifier world for mako-lexer-redos (denial_of_service).

The property: the Mako lexer parses any template in time roughly linear in its
length. CVE-2022-40023 is catastrophic backtracking in ``Lexer.match_tag_start``.
The pre-1.2.2 attribute repeat (``re.I | re.S | re.X``) treats each quoted span
as ``".*?"``, so a run of quotes with no closing ``>`` is split exponentially
many ways. Mako 1.2.2 stops a quoted span at the next quote of the same kind.
``parse`` runs ``mako.lexer.Lexer(template_text).parse()`` in a forked child and
records ``cpu_seconds`` when that work exceeds the contract threshold. A child
killed for exceeding its timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_TEMPLATE_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0


def _parse(template_text: str):
    import mako.lexer

    node = mako.lexer.Lexer(template_text).parse()
    return {"parsed": True, "node": type(node).__name__}


class World(BaseWorld):
    def parse(self, template_text: str, threshold_seconds: float = 2.0):
        """Parse ``template_text`` and record a CPU overrun past ``threshold_seconds``.

        Returns the child Outcome. ``cpu_seconds``, ``wall_seconds``, and
        ``killed`` come from that child. ``outcome.exceeded`` is True when CPU
        time, or a timeout kill, passed the threshold — the same condition that
        writes the resource capture.
        """
        text = require_text(template_text, "template_text", MAX_TEMPLATE_BYTES)
        if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, (int, float)):
            raise TypeError("threshold_seconds must be a real number")
        threshold = float(threshold_seconds)
        outcome = self.call(_parse, text, timeout=CHILD_TIMEOUT)
        cpu = outcome.cpu_seconds
        if outcome.killed and cpu <= threshold:
            cpu = threshold + 1.0
        outcome.exceeded = cpu > threshold
        if outcome.exceeded:
            capture.resource("cpu_seconds", cpu, threshold)
        return outcome
