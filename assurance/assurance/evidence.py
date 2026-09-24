"""Append-only evidence files. Hashes are a local integrity check, not a signature."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from assurance.schemas import EvidenceBundle


def write_bundle(path: Path, bundle: EvidenceBundle) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path.write_text(
        json.dumps({"sha256": digest, "bundle": payload}, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = [
        f"# Evidence {bundle.run_id}",
        "",
        f"sha256: `{digest}`",
        "",
        f"collector_enabled: {bundle.collector_enabled}",
        f"events: {len(bundle.events)}",
        "",
    ]
    for event in bundle.events:
        summary.append(
            f"- {event.actor_id} {event.tool_id} status={event.status_code} effect={event.effect}"
        )
    if bundle.telemetry_gaps:
        summary.extend(["", "## Gaps", *[f"- {gap}" for gap in bundle.telemetry_gaps]])
    (path.parent / "evidence.md").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )
    return digest


def verify_bundle(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = json.dumps(doc["bundle"], sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != doc.get("sha256"):
        raise ValueError("evidence hash mismatch")
    return digest
