"""Minimum viable promotion gate for QUALITY_PROMPT_VERSION."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CHAMPION_PATH = Path(__file__).resolve().parent / "champion.json"


def load_champion() -> dict[str, Any]:
    with CHAMPION_PATH.open() as fh:
        return json.load(fh)


def may_promote(
    challenger_brier: float,
    champion_brier: float,
    naive_brier: float,
) -> bool:
    """Strictly lower Brier than both the frozen champion and the naive baseline."""
    return challenger_brier < champion_brier and challenger_brier < naive_brier
