"""docs/cyber/runner-protocol.schema.json is generated from the Pydantic contracts."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.challenge import RunnerJob, RunnerResult

_SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "cyber" / "runner-protocol.schema.json"


def test_committed_schema_matches_pydantic_models():
    committed = json.loads(_SCHEMA.read_text())
    assert committed["job"] == RunnerJob.model_json_schema()
    assert committed["result"] == RunnerResult.model_json_schema()
