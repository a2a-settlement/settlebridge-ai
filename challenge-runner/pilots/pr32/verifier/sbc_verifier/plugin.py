"""pytest plugin loaded by run.py: provides the ``sbc_world`` fixture."""

from __future__ import annotations

import pytest

from sbc_verifier.world import World


@pytest.fixture
def sbc_world(monkeypatch):
    world = World(monkeypatch)
    try:
        yield world
    finally:
        world.close()
