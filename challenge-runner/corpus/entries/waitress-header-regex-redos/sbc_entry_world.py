"""Verifier world for waitress-header-regex-redos (denial_of_service).

The property: Waitress parses any request header in time roughly linear in its
length. CVE-2020-5236 was introduced in 1.4.2 when ``HEADER_FIELD`` was changed
to the RFC 7230 errata form; an invalid byte after a run of legal characters
makes that regex backtrack. ``parse_headers`` runs
``HTTPRequestParser.parse_header`` (the path that applies ``HEADER_FIELD``) in a
forked child and records ``cpu_seconds`` when the work exceeds the contract
threshold. A child killed for exceeding its timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_bytes

MAX_HEADER_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0
_REQUEST_LINE = b"GET / HTTP/1.1\r\n"


def _header_plus(raw_headers: bytes) -> bytes:
    """Turn a header block into the text ``parse_header`` expects.

    A buffer whose first line contains a colon is a bare header block, which is
    what the controls pass. ``parse_header`` wants a request line in front of
    that block and splits fields on CRLF. A buffer that already starts with a
    request line is returned unchanged.
    """
    first = raw_headers.split(b"\r\n", 1)[0]
    if b":" not in first and b" " in first:
        return raw_headers
    block = raw_headers if raw_headers.endswith(b"\r\n") else raw_headers + b"\r\n"
    return _REQUEST_LINE + block


def _parse_headers(raw_headers: bytes):
    from waitress.adjustments import Adjustments
    from waitress.parser import HTTPRequestParser

    parser = HTTPRequestParser(Adjustments())
    parser.parse_header(_header_plus(raw_headers))
    return {"header_count": len(parser.headers)}


class World(BaseWorld):
    def parse_headers(self, raw_headers: bytes, threshold_seconds: float = 2.0):
        """Parse ``raw_headers`` and record a CPU overrun past ``threshold_seconds``.

        Returns the child Outcome. ``cpu_seconds``, ``wall_seconds``, and
        ``killed`` come from that child. ``outcome.exceeded`` is True when CPU
        time, or a timeout kill, passed the threshold — the same condition that
        writes the resource capture.
        """
        raw = require_bytes(raw_headers, "raw_headers", MAX_HEADER_BYTES)
        if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, (int, float)):
            raise TypeError("threshold_seconds must be a real number")
        threshold = float(threshold_seconds)
        outcome = self.call(_parse_headers, raw, timeout=CHILD_TIMEOUT)
        cpu = outcome.cpu_seconds
        if outcome.killed and cpu <= threshold:
            cpu = threshold + 1.0
        outcome.exceeded = cpu > threshold
        if outcome.exceeded:
            capture.resource("cpu_seconds", cpu, threshold)
        return outcome
