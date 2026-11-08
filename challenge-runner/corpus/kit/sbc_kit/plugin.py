"""pytest plugin loaded by run.py: freezes the library digest and provides ``sbc_world``."""

from __future__ import annotations

import importlib.util

import pytest

from sbc_kit import worldbase

_world_module = None


def pytest_configure(config):
    global _world_module
    worldbase.freeze()
    spec = importlib.util.spec_from_file_location(
        "sbc_entry_world", worldbase.ROOT / "sbc_entry_world.py"
    )
    _world_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_world_module)


@pytest.fixture
def sbc_world():
    world = _world_module.World()
    try:
        yield world
    finally:
        world.close()
