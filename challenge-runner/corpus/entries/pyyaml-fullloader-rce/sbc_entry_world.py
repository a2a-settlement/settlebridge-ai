"""Verifier world for pyyaml-fullloader-rce (injection_execution).

The property: ``yaml.full_load`` and ``yaml.load`` with ``FullLoader`` do not execute
Python code taken from an untrusted YAML document.

``load`` runs the chosen loader against the library freshly imported from /work, in a
forked child whose audit hook records any code execution (``exec`` of string-compiled
code, ``os.system``, ``subprocess`` and the like). The verdict comes from that audit
record, never from a test's assertions: if the document made the library execute code,
the injection sentinel fires.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_DOCUMENT_BYTES = 64 * 1024
_LOADERS = {"full", "safe", "unsafe"}


def _load(text: str, loader: str):
    import yaml

    loaders = {"full": yaml.FullLoader, "safe": yaml.SafeLoader}
    if hasattr(yaml, "UnsafeLoader"):
        loaders["unsafe"] = yaml.UnsafeLoader
    else:
        loaders["unsafe"] = yaml.Loader
    return yaml.load(text, Loader=loaders[loader])


class World(BaseWorld):
    def load(self, document: str, loader: str = "full"):
        """Load ``document`` with the named loader. Records injection on code execution.

        Returns the child Outcome. ``outcome.executed`` is True when the library ran
        attacker-controlled code during the load.
        """
        text = require_text(document, "document", MAX_DOCUMENT_BYTES)
        if loader not in _LOADERS:
            raise ValueError(f"loader must be one of {sorted(_LOADERS)}")
        outcome = self.call(_load, text, loader, audit=True)
        outcome.executed = bool(outcome.events)
        if outcome.executed:
            capture.injection("command", f"yaml.load/{loader}: {outcome.events[0]}")
        return outcome
