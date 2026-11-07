"""Signed challenge reports: canonical JWS, no secrets, owner-or-operator visibility."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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
from app.schemas.challenge import ChallengeReport
from app.services.challenge_report import (
    ReportNotBuilt,
    ReportVerificationError,
    build_report,
    canonical_bytes,
    challenge_detail,
    sign_report,
    verify_report,
)

OWNER = uuid.UUID("11111111-1111-4111-a111-111111111111")
OPERATOR = uuid.UUID("22222222-2222-4222-a222-222222222222")
STRANGER = uuid.UUID("33333333-3333-4333-a333-333333333333")
OTHER = uuid.UUID("44444444-4444-4444-a444-444444444444")
TASK = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_TASK = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
BARE = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SEEN = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CLAIM_CONFIRMED = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
CLAIM_CLEAR = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
CLAIM_INCONCLUSIVE = uuid.UUID("abababab-abab-4aba-8aba-abababababab")
CLAIM_INVALID = uuid.UUID("cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd")
CLAIM_OPEN = uuid.UUID("efefefef-efef-4efe-8efe-efefefefefef")

BASE = "a" * 40
TARGET = "b" * 40
LATEST_HASH = "c" * 64
OLD_HASH = "ab" * 32
SOURCE = "d" * 64
WHEEL = "e" * 64
LOG_HASH = "2" * 64
IMAGE = "sha256:" + ("f" * 64)
FIXTURE = "sha256:" + ("1" * 64)
SCOPE = "Under the stated property, environment, and budget."
NO_FAILURE = "No failure found under the stated property, environment, and budget"
CONFIRMED_REASON = "Observer fired on the target. " + SCOPE
PROPERTY = "Rejected input stays rejected"
QUOTE = "rejected input stays rejected"
BACKEND = Path(__file__).resolve().parents[1]

SECRET_MARKERS = (
    "secret-signing-key-material",
    "secret-env-token",
    "secret-nested-key",
    "secret-latest-issue",
    "secret-old-issue",
    "secret-contract-token",
    "secret-storage-path",
    "secret-raw-log",
    "secret-meta-token",
    "secret-conflict",
    "secret-seen-token",
    "secret-attempt-log",
    "secret-expected",
    "secret-observed",
    "secret-impact",
    "secret-scenario",
    "secret-citation-token",
    "secret-evidence-token",
    "secret-control-password",
    "secret-observer-token",
    "secret-differential-token",
    "secret-old-verdict",
    "secret-task-evidence",
    "secret-undecided",
    "secret-other-finding",
    "secret-other-task-property",
    "secret-other-exec",
    "secret-other-reservation",
    "secret-other-idem",
    "secret-severity-token",
    "secret-idempotency",
    "secret-spend-idem",
    OLD_HASH,
)


class Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    async def commit(self):
        self.sync.commit()


def _run(coro):
    return asyncio.run(coro)


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    for model in (
        ChallengeTask,
        ChallengeContract,
        ChallengeParticipant,
        ChallengeAttempt,
        ChallengeClaim,
        ChallengeArtifact,
        ChallengeVerdict,
        Finding,
        SpendReservation,
        ExecutionUsage,
    ):
        model.__table__.create(eng)
    return eng


def _when(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def _keys():
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sample_report() -> ChallengeReport:
    return ChallengeReport(
        task_id=str(TASK),
        repo="acme/widget",
        pr_number=None,
        base_sha=BASE,
        target_sha=TARGET,
        contract_hash=LATEST_HASH,
        property_text="Rejected input stays rejected — propriété",
        environment={"python": "3.11", "limits": {"cpu": 1}},
        budget_cents=100,
        participants=[],
        claims=[],
        task_verdict="no_failure_found",
        digests={"image": IMAGE},
        usage={"cost_cents": 0, "cpu_seconds": 0, "model_tokens": 0, "wall_seconds": 0},
        issued_at=datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc),
    )


def _citation():
    return {
        "source": "property_clause",
        "reference": "property",
        "quote": QUOTE,
        "token": "secret-citation-token",
    }


def _evidence(*, rich: bool):
    body = {
        "citation_valid": True,
        "tamper_clean": True,
        "observer_fired": True,
        "access_token": "secret-evidence-token",
        "execution_ids": ["exec-positive"],
    }
    if not rich:
        return body
    body.update(
        {
            "observer_events": [
                {
                    "observer": "canary",
                    "kind": "canary_seen",
                    "detail": "fired",
                    "location": "challenge_tests/check.py",
                    "token": "secret-observer-token",
                }
            ],
            "controls": {
                "positive_ok": True,
                "negative_ok": True,
                "detail": "controls held",
                "password": "secret-control-password",
            },
            "differential": {
                "base_fired": False,
                "target_fired": True,
                "classification": "new_path",
                "token": "secret-differential-token",
            },
            "rederivation_fired": True,
            "reruns_reproduced": 3,
            "minimized_reproduced": True,
        }
    )
    return body


def _task(db, task_id, owner, *, property_text, idempotency):
    now = _when(1)
    db.add(
        ChallengeTask(
            id=task_id,
            mode="open_pr",
            owner_user_id=owner,
            installation_id=42,
            repo_full_name="acme/widget",
            pr_number=7,
            base_sha=BASE,
            target_sha=TARGET,
            property_text=property_text,
            failure_classes=["integrity"],
            environment={
                "python": "3.11",
                "install": ["pytest"],
                "github_token": "secret-env-token",
                "limits": {"cpu": 1, "api_key": "secret-nested-key"},
            },
            budget_cents=500,
            deadline_at=_when(20),
            state="closed",
            state_reason="budget exhausted",
            idempotency_key=idempotency,
            created_at=now,
            updated_at=now,
        )
    )


def _add_contract(db, task_id, version, content_hash, content, day):
    db.add(
        ChallengeContract(
            id=uuid.uuid4(),
            task_id=task_id,
            version=version,
            content_hash=content_hash,
            content=content,
            created_at=_when(day),
        )
    )


def seed(bridge: Bridge) -> None:
    db = bridge.sync
    _task(db, TASK, OWNER, property_text=PROPERTY, idempotency="secret-idempotency")
    _task(
        db,
        OTHER_TASK,
        OTHER,
        property_text="secret-other-task-property",
        idempotency="other-task",
    )
    _task(db, BARE, OWNER, property_text=PROPERTY, idempotency="bare-task")
    _add_contract(
        db,
        TASK,
        1,
        OLD_HASH,
        {"issue_body": "secret-old-issue", "token": "secret-contract-token"},
        2,
    )
    _add_contract(
        db,
        TASK,
        2,
        LATEST_HASH,
        {
            "issue_body": "secret-latest-issue",
            "token": "secret-contract-token",
            "fixtures_digest": FIXTURE,
        },
        3,
    )
    db.flush()
    author = uuid.uuid4()
    challenger = uuid.uuid4()
    db.add(
        ChallengeParticipant(
            id=author,
            task_id=TASK,
            role="author",
            operator_id=str(OWNER),
            platform_operated=False,
            conflict_declaration={"note": "secret-conflict"},
            artifacts_seen=[str(SEEN), "secret-seen-token"],
            created_at=_when(1),
        )
    )
    db.add(
        ChallengeParticipant(
            id=challenger,
            task_id=TASK,
            role="challenger",
            operator_id="platform:challenger",
            platform_operated=True,
            conflict_declaration={},
            artifacts_seen=[],
            created_at=_when(1, 13),
        )
    )
    db.add(
        ChallengeParticipant(
            id=uuid.uuid4(),
            task_id=TASK,
            role="verifier",
            operator_id="platform:verifier",
            platform_operated=True,
            conflict_declaration={},
            artifacts_seen=[],
            created_at=_when(1, 14),
        )
    )
    reservation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    contract_id = db.execute(
        select(ChallengeContract.id).where(
            ChallengeContract.task_id == TASK, ChallengeContract.version == 2
        )
    ).scalar_one()
    db.add(
        ChallengeAttempt(
            id=attempt_id,
            task_id=TASK,
            participant_id=challenger,
            contract_id=contract_id,
            state="submitted",
            reservation_id=reservation_id,
            error="secret-attempt-log",
            created_at=_when(4),
        )
    )
    specs = [
        (CLAIM_CONFIRMED, "confirmed", "Observer fired on the target", True, 5),
        (CLAIM_CLEAR, "no_failure_found", "observer stayed silent", False, 6),
        (CLAIM_INCONCLUSIVE, "inconclusive", "positive control failed", False, 7),
        (CLAIM_INVALID, "invalid_or_duplicate", "no citation", False, 8),
    ]
    for claim_id, verdict, reason, rich, day in specs:
        db.add(
            ChallengeClaim(
                id=claim_id,
                task_id=TASK,
                attempt_id=attempt_id,
                contract_id=contract_id,
                failure_class="integrity",
                scenario={
                    "precondition": "ready",
                    "setup": "secret-scenario",
                    "action": "send",
                    "test_path": "challenge_tests/check.py",
                },
                oracle_citation=_citation(),
                expected="secret-expected",
                observed="secret-observed",
                claimed_impact="secret-impact",
                state="decided",
                claim_hash="9" * 64,
                submitted_at=_when(day),
            )
        )
        if claim_id == CLAIM_CONFIRMED:
            db.add(
                ChallengeVerdict(
                    id=uuid.uuid4(),
                    task_id=TASK,
                    claim_id=claim_id,
                    contract_id=contract_id,
                    scope="claim",
                    verdict="invalid_or_duplicate",
                    decided_by="machine",
                    reason="secret-old-verdict",
                    evidence={"access_token": "secret-old-verdict"},
                    created_at=_when(day, 8),
                )
            )
        db.add(
            ChallengeVerdict(
                id=uuid.uuid4(),
                task_id=TASK,
                claim_id=claim_id,
                contract_id=contract_id,
                scope="claim",
                verdict=verdict,
                decided_by="machine",
                reason=reason,
                evidence=_evidence(rich=rich),
                created_at=_when(day, 18),
            )
        )
    db.add(
        ChallengeClaim(
            id=CLAIM_OPEN,
            task_id=TASK,
            attempt_id=attempt_id,
            contract_id=contract_id,
            failure_class="integrity",
            scenario={
                "precondition": "ready",
                "setup": "secret-scenario",
                "action": "send",
                "test_path": "challenge_tests/check.py",
            },
            oracle_citation=_citation(),
            expected="secret-expected",
            observed="secret-undecided",
            claimed_impact="secret-impact",
            state="submitted",
            claim_hash="8" * 64,
            submitted_at=_when(9),
        )
    )
    db.add(
        ChallengeVerdict(
            id=uuid.uuid4(),
            task_id=TASK,
            claim_id=None,
            contract_id=contract_id,
            scope="task",
            verdict="no_failure_found",
            decided_by="machine",
            reason="no claim fired the observer",
            evidence={"access_token": "secret-task-evidence"},
            created_at=_when(10),
        )
    )
    db.add(
        ChallengeArtifact(
            id=SEEN,
            task_id=TASK,
            tenant_user_id=OWNER,
            kind="source_tarball",
            content_hash=SOURCE,
            size_bytes=10,
            storage_path="secret-storage-path",
            meta={
                "stdout": "secret-raw-log",
                "access_token": "secret-meta-token",
                "image_digest": IMAGE,
            },
            created_at=_when(3),
        )
    )
    db.add(
        ChallengeArtifact(
            id=uuid.uuid4(),
            task_id=TASK,
            tenant_user_id=OWNER,
            kind="wheelhouse_manifest",
            content_hash=WHEEL,
            size_bytes=11,
            storage_path="secret-storage-path",
            meta={},
            created_at=_when(3, 13),
        )
    )
    db.add(
        ChallengeArtifact(
            id=uuid.uuid4(),
            task_id=TASK,
            tenant_user_id=OWNER,
            kind="run_log",
            content_hash=LOG_HASH,
            size_bytes=12,
            storage_path="secret-storage-path",
            meta={"stdout": "secret-raw-log"},
            created_at=_when(3, 14),
        )
    )
    db.add(
        Finding(
            id=uuid.uuid4(),
            tenant_user_id=OWNER,
            kind="challenge_gap",
            repo_full_name="acme/widget",
            task_id=TASK,
            claim_id=CLAIM_CONFIRMED,
            status="needs_decision",
            failure_class="integrity",
            title="Parser rejects a bad length",
            advisory_severity={
                "level": "high",
                "source": "GHSA-abcd",
                "token": "secret-severity-token",
            },
            target_sha=TARGET,
            dedupe_key="chal:acme/widget:integrity:abc",
            created_at=_when(11),
            updated_at=_when(11),
        )
    )
    db.add(
        Finding(
            id=uuid.uuid4(),
            tenant_user_id=OTHER,
            kind="challenge_gap",
            repo_full_name="acme/widget",
            task_id=TASK,
            claim_id=CLAIM_CONFIRMED,
            status="needs_decision",
            failure_class="integrity",
            title="secret-other-finding",
            advisory_severity=None,
            target_sha=TARGET,
            dedupe_key="chal:other",
            created_at=_when(11, 13),
            updated_at=_when(11, 13),
        )
    )
    db.add(
        SpendReservation(
            id=reservation_id,
            budget_id=uuid.uuid4(),
            subject=f"challenge_attempt:{attempt_id}",
            idempotency_key="secret-spend-idem",
            reserved_cents=100,
            used_cents=12,
            status="reconciled",
            expires_at=_when(20),
            created_at=_when(4),
            updated_at=_when(4),
        )
    )
    db.add(
        ExecutionUsage(
            id=uuid.uuid4(),
            reservation_id=reservation_id,
            execution_id="exec-charged",
            cause="initial",
            charged=True,
            cpu_seconds=3,
            wall_seconds=4,
            model_tokens=5,
            cost_cents=12,
            created_at=_when(4, 13),
        )
    )
    db.add(
        ExecutionUsage(
            id=uuid.uuid4(),
            reservation_id=reservation_id,
            execution_id="exec-retry",
            cause="platform_retry",
            charged=False,
            cpu_seconds=7,
            wall_seconds=8,
            model_tokens=9,
            cost_cents=99,
            created_at=_when(4, 14),
        )
    )
    other_reservation = uuid.uuid4()
    db.add(
        SpendReservation(
            id=other_reservation,
            budget_id=uuid.uuid4(),
            subject="secret-other-reservation",
            idempotency_key="secret-other-idem",
            reserved_cents=1000,
            used_cents=1000,
            status="reconciled",
            expires_at=_when(20),
            created_at=_when(4),
            updated_at=_when(4),
        )
    )
    db.add(
        ExecutionUsage(
            id=uuid.uuid4(),
            reservation_id=other_reservation,
            execution_id="secret-other-exec",
            cause="initial",
            charged=True,
            cpu_seconds=50,
            wall_seconds=50,
            model_tokens=50,
            cost_cents=1000,
            created_at=_when(4),
        )
    )
    db.commit()


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_OPERATOR_USER_IDS", str(OPERATOR))
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", "secret-signing-key-material")
    eng = _engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    seed(bridge)
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


def _assert_no_secrets(text: str) -> None:
    lowered = text.lower()
    assert "secure" not in lowered
    for marker in SECRET_MARKERS:
        assert marker not in text


def _outside_strings(text: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def _assert_sorted(value) -> None:
    if isinstance(value, dict):
        assert list(value) == sorted(value)
        for item in value.values():
            _assert_sorted(item)
    elif isinstance(value, list):
        for item in value:
            _assert_sorted(item)


def test_canonical_bytes_are_stable_sorted_and_utf8():
    report = _sample_report()
    first = canonical_bytes(report)
    second = canonical_bytes(report)
    assert first == second
    text = first.decode("utf-8")
    assert " " not in _outside_strings(text)
    assert "\n" not in text
    _assert_sorted(json.loads(text))
    assert "propriété".encode() in first
    assert "\\u00e9" not in text
    raw = json.loads(text)
    raw["access_token"] = "secret-file-token"
    reordered = {key: raw[key] for key in reversed(list(raw))}
    again = ChallengeReport.model_validate(reordered)
    assert canonical_bytes(again) == first
    assert b"secret-file-token" not in canonical_bytes(again)


def test_sign_verify_round_trip_and_rejections(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_KEY_ID", "challenge-report-1")
    private, public = _keys()
    other_private, other_public = _keys()
    report = _sample_report()
    token = sign_report(report, private)
    again = sign_report(report, private)
    parts = token.split(".")
    assert len(parts) == 3
    assert parts[1] == again.split(".")[1]
    assert len(_b64_decode(parts[2])) == 64
    header = json.loads(_b64_decode(parts[0]))
    assert header == {
        "alg": "ES256",
        "kid": "challenge-report-1",
        "typ": "challenge-report+jws",
    }
    verified = verify_report(token, public)
    assert verified.model_dump(mode="json") == report.model_dump(mode="json")
    assert verify_report(again, public).task_id == report.task_id

    payload = json.loads(_b64_decode(parts[1]))
    payload["repo"] = "evil/other"
    tampered_payload = f"{parts[0]}.{_b64(json.dumps(payload).encode())}.{parts[2]}"
    swapped_header = (
        f"{_b64(json.dumps({'alg': 'ES256', 'kid': 'attacker', 'typ': header['typ']}).encode())}"
        f".{parts[1]}.{parts[2]}"
    )
    with pytest.raises(ReportVerificationError):
        verify_report(tampered_payload, public)
    with pytest.raises(ReportVerificationError):
        verify_report(swapped_header, public)
    with pytest.raises(ReportVerificationError):
        verify_report(token, other_public)
    for alg in ("none", "None", "HS256"):
        forged_header = _b64(
            json.dumps({"alg": alg, "kid": "challenge-report-1", "typ": header["typ"]}).encode()
        )
        forged = f"{forged_header}.{parts[1]}.aaaa"
        with pytest.raises(ReportVerificationError):
            verify_report(forged, public)
        with pytest.raises(ReportVerificationError):
            verify_report(f"{forged_header}.{parts[1]}.", public)
    signed_by_other = sign_report(report, other_private)
    with pytest.raises(ReportVerificationError):
        verify_report(signed_by_other, public)


def test_report_omits_secrets_and_states_verdicts(world):
    report = _run(build_report(world, TASK))
    text = report.model_dump_json()
    _assert_no_secrets(text)
    assert report.contract_hash == LATEST_HASH
    assert report.property_text == PROPERTY
    assert report.task_verdict == "no_failure_found"
    assert report.scope_note == SCOPE
    assert report.environment == {
        "install": ["pytest"],
        "limits": {"cpu": 1},
        "python": "3.11",
    }
    assert report.digests == {
        "fixtures": FIXTURE,
        "image": IMAGE,
        "run_log": "sha256:" + LOG_HASH,
        "source": "sha256:" + SOURCE,
        "source_tarball": "sha256:" + SOURCE,
        "wheelhouse": "sha256:" + WHEEL,
        "wheelhouse_manifest": "sha256:" + WHEEL,
    }
    assert report.usage == {
        "cost_cents": 12,
        "cpu_seconds": 10,
        "model_tokens": 14,
        "wall_seconds": 12,
    }
    assert [row.operator_label for row in report.participants] == [
        str(OWNER),
        "platform:challenger",
        "platform:verifier",
    ]
    assert report.participants[0].artifacts_seen == [str(SEEN)]
    reasons = {row.verdict: row.reason for row in report.claims}
    assert reasons["no_failure_found"] == NO_FAILURE
    assert reasons["confirmed"] == CONFIRMED_REASON
    assert reasons["inconclusive"] == "positive control failed. " + SCOPE
    assert reasons["invalid_or_duplicate"] == "no citation. " + SCOPE
    assert all(SCOPE in row.reason or row.verdict == "no_failure_found" for row in report.claims)
    assert QUOTE in text
    assert "exec-positive" in text
    assert {row.claim_id for row in report.claims} == {
        str(CLAIM_CONFIRMED),
        str(CLAIM_CLEAR),
        str(CLAIM_INCONCLUSIVE),
        str(CLAIM_INVALID),
    }
    confirmed = next(row for row in report.claims if row.verdict == "confirmed")
    assert confirmed.evidence.controls is not None
    assert confirmed.evidence.controls.detail == "controls held"
    assert "secret-control-password" not in confirmed.model_dump_json()


def test_unbuilt_report_and_visibility(world, monkeypatch):
    with pytest.raises(ReportNotBuilt):
        _run(build_report(world, BARE))
    with pytest.raises(ReportNotBuilt):
        _run(build_report(world, uuid.uuid4()))

    owner = _run(challenge_detail(world, TASK, OWNER))
    operator = _run(challenge_detail(world, TASK, OPERATOR))
    assert owner is not None and operator is not None
    assert owner.id == operator.id == str(TASK)
    assert _run(challenge_detail(world, TASK, STRANGER)) is None
    assert _run(challenge_detail(world, uuid.uuid4(), OWNER)) is None
    assert _run(challenge_detail(world, OTHER_TASK, OWNER)) is None
    bare = _run(challenge_detail(world, BARE, OWNER))
    assert bare is not None and bare.report_url is None
    _assert_no_secrets(bare.model_dump_json())

    text = owner.model_dump_json()
    _assert_no_secrets(text)
    assert owner.summary.claims_submitted == 5
    assert owner.summary.confirmed == 1
    assert owner.summary.inconclusive == 1
    assert owner.summary.invalid == 1
    assert owner.summary.no_failure_found is True
    assert owner.scope_note == SCOPE
    assert owner.report_url == f"/api/v1/challenges/{TASK}/report"
    assert len(owner.verdicts) == 5
    task_verdict = next(row for row in owner.verdicts if row.claim_id is None)
    assert task_verdict.verdict == "no_failure_found"
    assert task_verdict.reason == NO_FAILURE
    assert owner.findings[0].title == "Parser rejects a bad length"
    assert owner.findings[0].advisory_severity is not None
    assert owner.findings[0].advisory_severity.source == "GHSA-abcd"
    assert owner.findings[0].oracle_citation is not None
    assert owner.findings[0].oracle_citation.quote == QUOTE
    assert len(owner.findings) == 1

    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", "  ")
    hidden = _run(challenge_detail(world, TASK, OWNER))
    assert hidden is not None and hidden.report_url is None


def test_cli_round_trip(tmp_path):
    private, public = _keys()
    report = _sample_report()
    payload = report.model_dump(mode="json")
    payload["access_token"] = "secret-file-token"
    report_path = tmp_path / "report.json"
    key_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    jws_path = tmp_path / "report.jws"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    key_path.write_text(private, encoding="utf-8")
    public_path.write_text(public, encoding="utf-8")
    env = os.environ.copy()
    env.pop("CHALLENGE_REPORT_SIGNING_KEY", None)
    env["CHALLENGE_REPORT_KEY_ID"] = "challenge-report-1"

    signed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.challenge_report",
            "sign",
            str(report_path),
            "--key-file",
            str(key_path),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert signed.returncode == 0, signed.stderr
    assert "BEGIN PRIVATE KEY" not in signed.stdout
    assert "BEGIN PRIVATE KEY" not in signed.stderr
    assert "secret-file-token" not in signed.stdout
    jws_path.write_text(signed.stdout, encoding="utf-8")
    token = signed.stdout.strip()
    body = json.loads(_b64_decode(token.split(".")[1]))
    assert "secret-file-token" not in json.dumps(body)
    assert body["repo"] == "acme/widget"

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.challenge_report",
            "verify",
            str(jws_path),
            "--public-key-file",
            str(public_path),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout == "ok\n"
    assert "BEGIN" not in verified.stdout + verified.stderr
    assert private not in verified.stdout + verified.stderr
    assert public not in verified.stdout + verified.stderr

    jws_path.write_text(token[:-4] + "aaaa\n", encoding="utf-8")
    rejected = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.challenge_report",
            "verify",
            str(jws_path),
            "--public-key-file",
            str(public_path),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert rejected.stderr == "verify failed\n"
    assert "BEGIN" not in rejected.stdout + rejected.stderr
    missing = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.challenge_report",
            "sign",
            str(report_path),
            "--key-file",
            str(tmp_path / "missing.pem"),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1
    assert missing.stderr == "sign failed\n"
    assert "BEGIN" not in missing.stdout + missing.stderr
