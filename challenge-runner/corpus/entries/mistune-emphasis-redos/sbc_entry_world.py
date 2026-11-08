"""Verifier world for mistune-emphasis-redos (denial_of_service).

The property: Mistune renders any Markdown emphasis in time roughly linear in
its length. CVE-2022-34749 is catastrophic backtracking in
``InlineParser.ASTERISK_EMPHASIS``. Before the fix, the body is

    (?:\\[\\*]|[^*])*(?:ESCAPE_TEXT|[^\\s*])

so a run of backslashes after an opening ``*`` with no closing marker is split
exponentially many ways. The fix consumes that body in one linear pass. ``render``
runs ``mistune.markdown(markdown_text)`` in a forked child and records
``cpu_seconds`` when that work exceeds the contract threshold. A child killed
for exceeding its timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_MARKDOWN_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0


def _render(markdown_text: str):
    import mistune

    html = mistune.markdown(markdown_text)
    return {"rendered": True, "html": html}


class World(BaseWorld):
    def render(self, markdown_text: str, threshold_seconds: float = 2.0):
        """Render ``markdown_text`` and record a CPU overrun past ``threshold_seconds``.

        Returns the child Outcome. ``cpu_seconds``, ``wall_seconds``, and
        ``killed`` come from that child. ``outcome.exceeded`` is True when CPU
        time, or a timeout kill, passed the threshold — the same condition that
        writes the resource capture.
        """
        text = require_text(markdown_text, "markdown_text", MAX_MARKDOWN_BYTES)
        if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, (int, float)):
            raise TypeError("threshold_seconds must be a real number")
        threshold = float(threshold_seconds)
        outcome = self.call(_render, text, timeout=CHILD_TIMEOUT)
        cpu = outcome.cpu_seconds
        if outcome.killed and cpu <= threshold:
            cpu = threshold + 1.0
        outcome.exceeded = cpu > threshold
        if outcome.exceeded:
            capture.resource("cpu_seconds", cpu, threshold)
        return outcome
