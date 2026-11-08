"""Verifier world for python-multipart-content-type-redos (denial_of_service).

The property: python-multipart parses any Content-Type header in time roughly
linear in its length. CVE-2024-24762 is catastrophic backtracking in
``parse_options_header``. The quoted-value alternative tries every split of a
backslash run when the closing quote is absent. The fix parses the header with
``email.message.Message`` instead. ``parse_content_type`` runs
``parse_options_header`` in a forked child and records ``cpu_seconds`` when
that work exceeds the contract threshold. A child killed for exceeding its
timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_HEADER_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1")
    if isinstance(value, str):
        return value
    raise TypeError("header parser returned a non-text token")


def _parse(content_type: str):
    from multipart.multipart import parse_options_header

    parsed, options = parse_options_header(content_type)
    if not isinstance(options, dict):
        raise TypeError("header parser did not return parameters")
    return {
        "content_type": _text(parsed),
        "options": {_text(key): _text(value) for key, value in options.items()},
    }


class World(BaseWorld):
    def parse_content_type(self, content_type: str, threshold_seconds: float = 2.0):
        """Parse ``content_type`` and record a CPU overrun past ``threshold_seconds``.

        Returns the child Outcome. ``cpu_seconds``, ``wall_seconds``, and
        ``killed`` come from that child. ``outcome.exceeded`` is True when CPU
        time, or a timeout kill, passed the threshold — the same condition that
        writes the resource capture.
        """
        header = require_text(content_type, "content_type", MAX_HEADER_BYTES)
        if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, (int, float)):
            raise TypeError("threshold_seconds must be a real number")
        threshold = float(threshold_seconds)
        outcome = self.call(_parse, header, timeout=CHILD_TIMEOUT)
        cpu = outcome.cpu_seconds
        if outcome.killed and cpu <= threshold:
            cpu = threshold + 1.0
        outcome.exceeded = cpu > threshold
        if outcome.exceeded:
            capture.resource("cpu_seconds", cpu, threshold)
        return outcome
