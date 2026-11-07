"""Signed evidence report and read models (owner: P1-5).

The report is a ChallengeReport serialized canonically (sorted keys, no whitespace)
and signed as a compact JWS with ES256 using CHALLENGE_REPORT_SIGNING_KEY. It never
contains tokens, raw logs, or another tenant's data.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import (
    ChallengeArtifact,
    ChallengeAttempt,
    ChallengeClaim,
    ChallengeContract,
    ChallengeParticipant,
    ChallengeTask,
    ChallengeVerdict,
    Finding,
)
from app.models.spend import ExecutionUsage, SpendReservation
from app.schemas.challenge import (
    AdvisorySeverity,
    ChallengeDetailOut,
    ChallengeReport,
    ChallengeSummary,
    FindingOut,
    OracleCitation,
    ParticipantOut,
    ReportClaim,
    ReportParticipant,
    ValidationEvidence,
    VerdictOut,
)

# JWS ES256 is the raw 32-byte R || 32-byte S pair, not the DER encoding
# cryptography returns from ECDSA.sign.
_COORD_BYTES = 32
_SCOPE_SENTENCE = "Under the stated property, environment, and budget."
_NO_FAILURE_STATEMENT = "No failure found under the stated property, environment, and budget"
_EXCLUDED_ENV_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|private[_-]?key|"
    r"signing[_-]?key|credential|issue[_-]?body|raw[_-]?log|stdout|stderr)",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_ROLE_ORDER = ("author", "solver", "challenger", "verifier", "adjudicator")
_DIGEST_FIELDS = {
    "fixtures_digest": "fixtures",
    "image_digest": "image",
    "source_digest": "source",
    "wheelhouse_manifest": "wheelhouse",
}
_NESTED_DIGEST_KEYS = ("result", "runner", "runner_result")
_MAX_JWS_CHARS = 1_000_000
_DROP = object()


class ReportNotBuilt(Exception):
    """The task has no frozen contract or task verdict that can be signed."""


class ReportVerificationError(Exception):
    """The compact JWS is not an ES256 challenge report for this key."""


def canonical_bytes(report: ChallengeReport) -> bytes:
    """Sorted-key JSON, no insignificant whitespace, UTF-8, from JSON mode."""
    payload = report.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign_report(report: ChallengeReport, private_key_pem: str) -> str:
    header = {
        "alg": "ES256",
        "kid": settings.CHALLENGE_REPORT_KEY_ID,
        "typ": "challenge-report+jws",
    }
    header_b64 = _b64url(_json_bytes(header))
    payload_b64 = _b64url(canonical_bytes(report))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    private_key = _load_private_key(private_key_pem)
    try:
        der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        signature = _der_to_raw(der)
    except ReportVerificationError:
        raise
    except Exception:
        raise ReportVerificationError("invalid signing key") from None
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def verify_report(jws: str, public_key_pem: str) -> ChallengeReport:
    if not isinstance(jws, str):
        raise ReportVerificationError("invalid jws")
    token = jws.strip()
    if not token or len(token) > _MAX_JWS_CHARS:
        raise ReportVerificationError("invalid jws")
    parts = token.split(".")
    if len(parts) != 3 or any(part == "" for part in parts):
        raise ReportVerificationError("invalid jws")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
    except ReportVerificationError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise ReportVerificationError("invalid jws") from None
    # The header does not choose the algorithm. none, HS256, and anything
    # other than the exact string ES256 are rejected before verification.
    if not isinstance(header, dict) or type(header.get("alg")) is not str:
        raise ReportVerificationError("unsupported alg")
    if header.get("alg") != "ES256":
        raise ReportVerificationError("unsupported alg")
    if len(signature) != _COORD_BYTES * 2:
        raise ReportVerificationError("invalid signature")
    public_key = _load_public_key(public_key_pem)
    try:
        r = int.from_bytes(signature[:_COORD_BYTES], "big")
        s = int.from_bytes(signature[_COORD_BYTES:], "big")
        der = encode_dss_signature(r, s)
        public_key.verify(
            der,
            f"{header_b64}.{payload_b64}".encode("ascii"),
            ec.ECDSA(hashes.SHA256()),
        )
    except ReportVerificationError:
        raise
    except Exception:
        raise ReportVerificationError("invalid signature") from None
    if header.get("typ") != "challenge-report+jws":
        raise ReportVerificationError("invalid jws")
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ReportVerificationError("invalid payload")
        return ChallengeReport.model_validate(data)
    except ReportVerificationError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValidationError, ValueError):
        raise ReportVerificationError("invalid payload") from None


def signing_key_configured() -> bool:
    key = settings.CHALLENGE_REPORT_SIGNING_KEY
    return isinstance(key, str) and key.strip() != ""


def is_challenge_operator(user_id: uuid.UUID) -> bool:
    parsed = _as_uuid(user_id)
    return parsed is not None and parsed in _operator_ids()


async def build_report(db: AsyncSession, task_id: uuid.UUID) -> ChallengeReport:
    task_uuid = _as_uuid(task_id)
    if task_uuid is None:
        raise ReportNotBuilt()
    task = await _task(db, task_uuid)
    if task is None:
        raise ReportNotBuilt()
    contract = await _latest_contract(db, task_uuid)
    if contract is None or not contract.content_hash:
        raise ReportNotBuilt()
    claims = await _claims(db, task_uuid)
    verdict_rows = await _verdicts(db, task_uuid)
    task_verdict, by_claim = _split_verdicts(verdict_rows)
    if task_verdict is None:
        raise ReportNotBuilt()
    participants = await _participants(db, task_uuid)
    artifacts = await _artifacts(db, task_uuid)
    report_claims = []
    for claim in _sorted_claims(claims):
        claim_id = _as_uuid(claim.id)
        if claim_id is None or claim_id not in by_claim:
            continue
        report_claims.append(_report_claim(claim, by_claim[claim_id]))
    return ChallengeReport(
        task_id=str(task.id),
        repo=task.repo_full_name,
        pr_number=task.pr_number,
        base_sha=task.base_sha,
        target_sha=task.target_sha,
        contract_hash=contract.content_hash,
        property_text=task.property_text,
        environment=_public_environment(task.environment),
        budget_cents=int(task.budget_cents),
        participants=[_report_participant(row) for row in _sorted_participants(participants)],
        claims=report_claims,
        task_verdict=task_verdict.verdict,
        digests=_digests(artifacts, contract.content),
        usage=await _usage_totals(db, task_uuid, [claim.id for claim in claims]),
        issued_at=_now(),
    )


async def challenge_detail(
    db: AsyncSession, task_id: uuid.UUID, viewer_user_id: uuid.UUID
) -> ChallengeDetailOut | None:
    """Returns None for a task the viewer may not see (callers answer 404)."""
    task_uuid = _as_uuid(task_id)
    viewer = _as_uuid(viewer_user_id)
    if task_uuid is None or viewer is None:
        return None
    task = await _task(db, task_uuid)
    if task is None or not _can_view(task, viewer):
        return None
    claims = await _claims(db, task_uuid)
    verdict_rows = await _verdicts(db, task_uuid)
    task_verdict, by_claim = _split_verdicts(verdict_rows)
    participants = await _participants(db, task_uuid)
    contract = await _latest_contract(db, task_uuid)
    findings = await _findings(db, task_uuid, _as_uuid(task.owner_user_id))
    citations = {_as_uuid(claim.id): claim.oracle_citation for claim in claims}
    return ChallengeDetailOut(
        id=str(task.id),
        repo=task.repo_full_name,
        pr_number=task.pr_number,
        base_sha=task.base_sha,
        target_sha=task.target_sha,
        state=task.state,
        state_reason=task.state_reason,
        property_text=task.property_text,
        participants=[
            ParticipantOut(
                role=row.role,
                operator_label=row.operator_id,
                platform_operated=bool(row.platform_operated),
            )
            for row in _sorted_participants(participants)
        ],
        summary=_summary(claims, by_claim, task_verdict),
        verdicts=_detail_verdicts(task_verdict, by_claim),
        findings=[_finding_out(row, task.id, citations) for row in _sorted_findings(findings)],
        report_url=_report_url(task.id, contract=contract, task_verdict=task_verdict),
    )


def verdict_statement(verdict: str, reason: str | None) -> str:
    if verdict == "no_failure_found":
        return _NO_FAILURE_STATEMENT
    text = reason or ""
    text = " ".join(text.split()).strip().strip(".").strip()
    if _SCOPE_SENTENCE in text:
        return text
    if not text:
        return _SCOPE_SENTENCE
    return f"{text}. {_SCOPE_SENTENCE}"


def _report_url(task_id, *, contract, task_verdict) -> str | None:
    if contract is None or task_verdict is None or not signing_key_configured():
        return None
    return f"/api/v1/challenges/{task_id}/report"


def _summary(claims, by_claim, task_verdict) -> ChallengeSummary:
    counts = {"confirmed": 0, "inconclusive": 0, "invalid_or_duplicate": 0}
    for claim in claims:
        verdict = by_claim.get(_as_uuid(claim.id))
        if verdict is not None and verdict.verdict in counts:
            counts[verdict.verdict] += 1
    return ChallengeSummary(
        claims_submitted=len(claims),
        confirmed=counts["confirmed"],
        inconclusive=counts["inconclusive"],
        invalid=counts["invalid_or_duplicate"],
        no_failure_found=(task_verdict is not None and task_verdict.verdict == "no_failure_found"),
    )


def _detail_verdicts(task_verdict, by_claim) -> list[VerdictOut]:
    rows = []
    if task_verdict is not None:
        rows.append(task_verdict)
    rows.extend(by_claim.values())
    rows.sort(key=lambda row: (_aware(row.created_at), str(row.id)))
    return [
        VerdictOut(
            claim_id=None if row.claim_id is None or row.scope == "task" else str(row.claim_id),
            verdict=row.verdict,
            decided_by=row.decided_by,
            reason=verdict_statement(row.verdict, row.reason),
            created_at=_aware(row.created_at),
        )
        for row in rows
    ]


def _finding_out(row, task_id, citations):
    citation = None
    claim_id = _as_uuid(row.claim_id)
    if claim_id is not None:
        citation = _citation(citations.get(claim_id))
    return FindingOut(
        id=str(row.id),
        kind=row.kind,
        repo=row.repo_full_name,
        status=row.status,
        failure_class=row.failure_class,
        title=row.title,
        advisory_severity=_severity(row.advisory_severity),
        oracle_citation=citation,
        target_sha=row.target_sha,
        evidence_url=f"/api/v1/challenges/{task_id}/report",
        created_at=_aware(row.created_at),
    )


def _report_claim(claim, verdict) -> ReportClaim:
    citation = _citation(claim.oracle_citation)
    if citation is None:
        raise ReportNotBuilt()
    return ReportClaim(
        claim_id=str(claim.id),
        failure_class=claim.failure_class,
        oracle_citation=citation,
        verdict=verdict.verdict,
        reason=verdict_statement(verdict.verdict, verdict.reason),
        evidence=_evidence(verdict.evidence),
    )


def _report_participant(row) -> ReportParticipant:
    return ReportParticipant(
        role=row.role,
        operator_label=row.operator_id,
        platform_operated=bool(row.platform_operated),
        artifacts_seen=_artifact_ids(row.artifacts_seen),
    )


def _can_view(task, viewer: uuid.UUID) -> bool:
    return _as_uuid(task.owner_user_id) == viewer or viewer in _operator_ids()


def _operator_ids() -> frozenset[uuid.UUID]:
    found: list[uuid.UUID] = []
    for part in settings.CHALLENGE_OPERATOR_USER_IDS.split(","):
        parsed = _as_uuid(part.strip())
        if parsed is not None:
            found.append(parsed)
    return frozenset(found)


def _split_verdicts(rows):
    ordered = sorted(rows, key=lambda row: (_aware(row.created_at), str(row.id)), reverse=True)
    task_verdict = None
    by_claim = {}
    for row in ordered:
        if row.scope == "task" or row.claim_id is None:
            if task_verdict is None:
                task_verdict = row
            continue
        claim_id = _as_uuid(row.claim_id)
        if claim_id is None or claim_id in by_claim:
            continue
        by_claim[claim_id] = row
    return task_verdict, by_claim


def _sorted_participants(rows):
    def key(row):
        try:
            rank = _ROLE_ORDER.index(row.role)
        except ValueError:
            rank = len(_ROLE_ORDER)
        return (rank, row.operator_id)

    return sorted(rows, key=key)


def _sorted_claims(rows):
    return sorted(rows, key=lambda row: (_aware(row.submitted_at), str(row.id)))


def _sorted_findings(rows):
    return sorted(rows, key=lambda row: (_aware(row.created_at), str(row.id)))


def _digests(artifacts, contract_content) -> dict[str, str]:
    latest = {}
    for artifact in artifacts:
        current = latest.get(artifact.kind)
        if current is None or _newer(artifact, current):
            latest[artifact.kind] = artifact
    digests: dict[str, str] = {}
    for kind in sorted(latest):
        digest = _as_digest(latest[kind].content_hash)
        if digest is not None:
            digests[kind] = digest
        for source in _digest_sources(latest[kind].meta):
            _take_digests(digests, source)
    if "source" not in digests and "source_tarball" in digests:
        digests["source"] = digests["source_tarball"]
    if "wheelhouse" not in digests and "wheelhouse_manifest" in digests:
        digests["wheelhouse"] = digests["wheelhouse_manifest"]
    for source in _digest_sources(contract_content):
        _take_digests(digests, source)
    return digests


def _digest_sources(raw) -> list[dict]:
    mapping = _as_dict(raw)
    if not mapping:
        return []
    sources = [mapping]
    for key in _NESTED_DIGEST_KEYS:
        nested = mapping.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    return sources


def _take_digests(digests: dict[str, str], source: dict) -> None:
    for source_key, dest_key in _DIGEST_FIELDS.items():
        if dest_key in digests:
            continue
        digest = _as_digest(source.get(source_key))
        if digest is not None:
            digests[dest_key] = digest


def _as_digest(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if _DIGEST.fullmatch(text):
        return text
    if _HEX64.fullmatch(text):
        return f"sha256:{text}"
    return None


def _newer(left, right) -> bool:
    return (_aware(left.created_at), str(left.id)) > (_aware(right.created_at), str(right.id))


async def _usage_totals(db, task_id, claim_ids) -> dict[str, int]:
    attempts = await _scalars(
        db, select(ChallengeAttempt).where(ChallengeAttempt.task_id == task_id)
    )
    reservation_ids = [row.reservation_id for row in attempts if row.reservation_id is not None]
    subjects = [f"challenge_baseline:{task_id}"]
    subjects.extend(f"challenge_attempt:{row.id}" for row in attempts)
    subjects.extend(f"challenge_validate:{claim_id}" for claim_id in claim_ids)
    filters = []
    if reservation_ids:
        filters.append(SpendReservation.id.in_(reservation_ids))
    for subject in subjects:
        filters.append(SpendReservation.subject == subject)
        filters.append(SpendReservation.subject.startswith(f"{subject}:"))
        filters.append(SpendReservation.subject.startswith(f"spend:{subject}"))
    found = list(reservation_ids)
    if filters:
        result = await db.execute(select(SpendReservation.id).where(or_(*filters)))
        found.extend(result.scalars().all())
    unique = []
    seen = set()
    for item in found:
        parsed = _as_uuid(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        unique.append(parsed)
    if not unique:
        return _zero_usage()
    rows = await _scalars(
        db, select(ExecutionUsage).where(ExecutionUsage.reservation_id.in_(unique))
    )
    return _sum_usage(rows)


def _zero_usage() -> dict[str, int]:
    return {"cost_cents": 0, "cpu_seconds": 0, "model_tokens": 0, "wall_seconds": 0}


def _sum_usage(rows) -> dict[str, int]:
    totals = _zero_usage()
    for row in rows:
        totals["cpu_seconds"] += int(row.cpu_seconds or 0)
        totals["wall_seconds"] += int(row.wall_seconds or 0)
        totals["model_tokens"] += int(row.model_tokens or 0)
        if row.charged:
            totals["cost_cents"] += int(row.cost_cents or 0)
    return totals


def _public_environment(raw) -> dict:
    mapping = _as_dict(raw)
    return _public_mapping(mapping)


def _public_mapping(mapping: dict) -> dict:
    cleaned = {}
    for key, value in mapping.items():
        if not isinstance(key, str) or _EXCLUDED_ENV_KEY.search(key):
            continue
        safe = _json_safe(value)
        if safe is _DROP:
            continue
        cleaned[key] = safe
    return cleaned


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and value != value:
            return None
        return value
    if isinstance(value, dict):
        return _public_mapping(value)
    if isinstance(value, list):
        items = []
        for item in value:
            safe = _json_safe(item)
            if safe is not _DROP:
                items.append(safe)
        return items
    return _DROP


def _artifact_ids(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        text = str(item) if isinstance(item, uuid.UUID) else item
        if isinstance(text, str) and _UUID_RE.fullmatch(text):
            found.append(str(uuid.UUID(text)))
    return found


def _citation(raw) -> OracleCitation | None:
    mapping = _as_dict(raw)
    if not mapping:
        return None
    body = {key: mapping[key] for key in ("source", "reference", "quote") if key in mapping}
    try:
        return OracleCitation.model_validate(body)
    except ValidationError:
        return None


def _severity(raw) -> AdvisorySeverity | None:
    mapping = _as_dict(raw)
    if not mapping:
        return None
    body = {key: mapping[key] for key in ("level", "source") if key in mapping}
    try:
        return AdvisorySeverity.model_validate(body)
    except ValidationError:
        return None


def _evidence(raw) -> ValidationEvidence:
    mapping = _as_dict(raw)
    body: dict = {}
    for key in (
        "citation_valid",
        "tamper_clean",
        "observer_fired",
        "rederivation_fired",
        "reruns_reproduced",
        "minimized_reproduced",
        "needs_adjudication",
    ):
        if key in mapping:
            body[key] = mapping[key]
    events = []
    for item in mapping.get("observer_events") or []:
        if not isinstance(item, dict):
            continue
        events.append(
            {key: item[key] for key in ("observer", "kind", "detail", "location") if key in item}
        )
    body["observer_events"] = events
    controls = mapping.get("controls")
    if isinstance(controls, dict):
        body["controls"] = {
            key: controls[key]
            for key in ("positive_ok", "negative_ok", "detail")
            if key in controls
        }
    differential = mapping.get("differential")
    if isinstance(differential, dict):
        body["differential"] = {
            key: differential[key]
            for key in ("base_fired", "target_fired", "reference_fired", "classification")
            if key in differential
        }
    body["execution_ids"] = [
        item for item in mapping.get("execution_ids") or [] if isinstance(item, str) and item
    ]
    return ValidationEvidence.model_validate(body)


def _as_dict(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    if not data or _B64URL.fullmatch(data) is None:
        raise ReportVerificationError("invalid jws")
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _load_private_key(pem: str):
    if not isinstance(pem, str):
        raise ReportVerificationError("invalid signing key")
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ReportVerificationError("invalid signing key") from None
    _require_p256(key, "invalid signing key")
    return key


def _load_public_key(pem: str):
    if not isinstance(pem, str):
        raise ReportVerificationError("invalid signature")
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ReportVerificationError("invalid signature") from None
    _require_p256(key, "invalid signature")
    return key


def _require_p256(key, message: str) -> None:
    curve = getattr(key, "curve", None)
    if curve is None or curve.name != "secp256r1":
        raise ReportVerificationError(message)


def _der_to_raw(der: bytes) -> bytes:
    try:
        r_value, s_value = decode_dss_signature(der)
        return r_value.to_bytes(_COORD_BYTES, "big") + s_value.to_bytes(_COORD_BYTES, "big")
    except (ValueError, OverflowError):
        raise ReportVerificationError("invalid signing key") from None


async def _task(db, task_id):
    return await _scalar(db, select(ChallengeTask).where(ChallengeTask.id == task_id))


async def _latest_contract(db, task_id):
    stmt = (
        select(ChallengeContract)
        .where(ChallengeContract.task_id == task_id)
        .order_by(ChallengeContract.version.desc())
        .limit(1)
    )
    return await _scalar(db, stmt)


async def _participants(db, task_id):
    return await _scalars(
        db, select(ChallengeParticipant).where(ChallengeParticipant.task_id == task_id)
    )


async def _claims(db, task_id):
    return await _scalars(db, select(ChallengeClaim).where(ChallengeClaim.task_id == task_id))


async def _verdicts(db, task_id):
    return await _scalars(db, select(ChallengeVerdict).where(ChallengeVerdict.task_id == task_id))


async def _artifacts(db, task_id):
    return await _scalars(db, select(ChallengeArtifact).where(ChallengeArtifact.task_id == task_id))


async def _findings(db, task_id, owner_id):
    rows = await _scalars(db, select(Finding).where(Finding.task_id == task_id))
    if owner_id is None:
        return []
    return [row for row in rows if _as_uuid(row.tenant_user_id) == owner_id]


async def _scalar(db, stmt):
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _scalars(db, stmt):
    result = await db.execute(stmt)
    return list(result.scalars().all())
