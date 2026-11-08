"""Verifier world for setuptools-package-index-redos (denial_of_service).

The property: setuptools.package_index parses any HTML page in time roughly
linear in its length. CVE-2022-40897 is backtracking in the ``REL`` pattern
``find_external_links`` uses to read homepage and download links. Before the
fix, several quantifiers can consume the same spaces in an unclosed tag, so
the matcher retries many partitions of that run. The fix bounds the whitespace
around ``rel`` and stops the attribute value at a space. ``parse_page`` runs
``find_external_links`` on the page in a forked child and records
``cpu_seconds`` when that work exceeds the contract threshold. A child killed
for exceeding its timeout counts as exceeding.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text


def _load_sbc_collect() -> None:
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent
    for candidate in (root / "sbc_collect.py", root / "controls" / "sbc_collect.py"):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("sbc_collect", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return


_load_sbc_collect()

MAX_PAGE_BYTES = 64 * 1024
CHILD_TIMEOUT = 8.0
_INDEX_URL = "https://pypi.org/simple/demo/"


def _parse(page: str):
    import setuptools.package_index as package_index

    links = list(package_index.find_external_links(_INDEX_URL, page))
    return {"parsed": True, "links": links}


class World(BaseWorld):
    def parse_page(self, page: str, threshold_seconds: float = 2.0):
        """Parse ``page`` and record a CPU overrun past ``threshold_seconds``.

        Returns the child Outcome. ``cpu_seconds``, ``wall_seconds``, and
        ``killed`` come from that child. ``outcome.exceeded`` is True when CPU
        time, or a timeout kill, passed the threshold — the same condition that
        writes the resource capture.
        """
        text = require_text(page, "page", MAX_PAGE_BYTES)
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
