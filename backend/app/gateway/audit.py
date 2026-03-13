"""Audit logger with Merkle tree integrity and CSV/JSON export."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gateway import AuditEntry, PolicyDecisionType

logger = logging.getLogger(__name__)

# Optional Merkle tree from a2a-settlement compliance module
_MerkleTree = None
try:
    from compliance.merkle import MerkleTree as _MT

    _MerkleTree = _MT
except ImportError:
    logger.info("compliance.merkle not available; Merkle integrity disabled")


def _hash_request(source: str, target: str, escrow_id: str | None, ts: str) -> str:
    payload = f"{source}:{target}:{escrow_id or ''}:{ts}"
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditLogger:
    """Append-only audit log backed by Postgres with optional Merkle integrity."""

    def __init__(self, merkle_db_path: str | None = None) -> None:
        self._tree = None
        if _MerkleTree and merkle_db_path:
            self._tree = _MerkleTree(Path(merkle_db_path))
            logger.info("Merkle tree initialised at %s", merkle_db_path)

    @property
    def merkle_root(self) -> str | None:
        if self._tree:
            return self._tree.root
        return None

    async def log(
        self,
        session: AsyncSession,
        *,
        source_agent: str,
        target_agent: str,
        policy_decision: PolicyDecisionType,
        escrow_id: str | None = None,
        latency_ms: int | None = None,
        response_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        now = datetime.now(timezone.utc)
        request_hash = _hash_request(source_agent, target_agent, escrow_id, now.isoformat())

        merkle_root = None
        if self._tree:
            try:
                from compliance.models import (
                    AttestationHeader,
                    PreDisputeAttestationPayload,
                )

                header = AttestationHeader(
                    version="1.0",
                    schema_id="gateway-audit",
                    created_at=now.isoformat(),
                    issuer_id="settlebridge-gateway",
                    nonce=uuid.uuid4().hex,
                )
                payload = PreDisputeAttestationPayload(header=header)
                root, _ = self._tree.append(payload)
                merkle_root = root
            except Exception:
                logger.debug("Merkle append failed, continuing without integrity")

        entry = AuditEntry(
            timestamp=now,
            request_hash=request_hash,
            source_agent=source_agent,
            target_agent=target_agent,
            policy_decision=policy_decision,
            escrow_id=escrow_id,
            latency_ms=latency_ms,
            response_status=response_status,
            merkle_root=merkle_root,
            details=details,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry

    async def query(
        self,
        session: AsyncSession,
        *,
        source_agent: str | None = None,
        target_agent: str | None = None,
        decision: PolicyDecisionType | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditEntry], int]:
        stmt = select(AuditEntry)
        count_stmt = select(func.count(AuditEntry.id))

        if source_agent:
            stmt = stmt.where(AuditEntry.source_agent == source_agent)
            count_stmt = count_stmt.where(AuditEntry.source_agent == source_agent)
        if target_agent:
            stmt = stmt.where(AuditEntry.target_agent == target_agent)
            count_stmt = count_stmt.where(AuditEntry.target_agent == target_agent)
        if decision:
            stmt = stmt.where(AuditEntry.policy_decision == decision)
            count_stmt = count_stmt.where(AuditEntry.policy_decision == decision)

        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(AuditEntry.timestamp.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    def export_csv(self, entries: list[AuditEntry]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "timestamp", "request_hash", "source_agent", "target_agent",
            "policy_decision", "escrow_id", "latency_ms", "response_status", "merkle_root",
        ])
        for e in entries:
            writer.writerow([
                str(e.id), e.timestamp.isoformat(), e.request_hash,
                e.source_agent, e.target_agent, e.policy_decision.value,
                e.escrow_id or "", e.latency_ms or "", e.response_status or "",
                e.merkle_root or "",
            ])
        return output.getvalue()

    def export_json(self, entries: list[AuditEntry]) -> str:
        records = []
        for e in entries:
            records.append({
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "request_hash": e.request_hash,
                "source_agent": e.source_agent,
                "target_agent": e.target_agent,
                "policy_decision": e.policy_decision.value,
                "escrow_id": e.escrow_id,
                "latency_ms": e.latency_ms,
                "response_status": e.response_status,
                "merkle_root": e.merkle_root,
            })
        return json.dumps({"entries": records, "merkle_root": self.merkle_root}, indent=2)

    def close(self) -> None:
        if self._tree:
            self._tree.close()
