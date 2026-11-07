"""GET /api/v1/cyber/tasks/{bounty_id} is read-only and hides non-public bounties."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.github import (
    AcceptanceSpec,
    GitHubAlertDispatch,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
)
from app.routes.cyber import router
from app.schemas.challenge import ChecksResult, CyberTaskDetailOut
from app.services import cyber_tasks

HEAD = "0123456789abcdef0123456789abcdef01234567"
FAILED_HEAD = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
WHEN = datetime(2026, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
SECRET = "SECRET_DETAIL_LEAK"
NOT_FOUND = {"detail": "Not Found"}
TABLES = (
    "bounties",
    "github_installations",
    "github_repo_links",
    "github_bounty_bindings",
    "github_acceptance_specs",
    "github_alert_dispatches",
    "github_leases",
    "github_settlements",
)


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE bounties ("
                "id CHAR(32) NOT NULL PRIMARY KEY, "
                "title VARCHAR(500) NOT NULL, "
                "status VARCHAR(32) NOT NULL)"
            )
        )
    for model in (
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        AcceptanceSpec,
        GitHubAlertDispatch,
        GitHubLease,
        GitHubSettlement,
    ):
        model.__table__.create(eng)
    return eng


def _snapshot(sync: Session) -> tuple:
    dumped = []
    for table in TABLES:
        rows = sync.execute(text(f"SELECT * FROM {table}")).mappings().all()
        normalized = tuple(
            sorted(
                tuple(sorted((key, "" if value is None else str(value)) for key, value in row.items()))
                for row in rows
            )
        )
        dumped.append((table, len(rows), normalized))
    return tuple(dumped)


def _client_for(bridge: _Bridge) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _insert_bounty(db: _Bridge, bounty_id: uuid.UUID, title: str, status: str = "OPEN") -> None:
    db.sync.execute(
        text("INSERT INTO bounties (id, title, status) VALUES (:id, :title, :status)"),
        {"id": bounty_id.hex, "title": title, "status": status},
    )


def _add(db: _Bridge, *, repo: str, title: str, **opts) -> str:
    bounty_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    _insert_bounty(db, bounty_id, title, opts.get("bounty_status", "OPEN"))
    db.sync.add(
        GitHubBountyBinding(
            id=binding_id,
            bounty_id=bounty_id,
            repo_full_name=repo,
            issue_number=opts["issue"],
            current_spec_id=spec_id,
            pr_number=opts.get("pr"),
            state=opts.get("state", "advertised"),
            evaluated_head_sha=opts.get("evaluated_head_sha"),
            last_assessment=opts.get("last_assessment"),
            created_at=WHEN,
            updated_at=WHEN,
        )
    )
    db.sync.add(
        AcceptanceSpec(
            id=spec_id,
            binding_id=binding_id,
            version=1,
            content_hash=f"hash-{title}",
            test_command=SECRET,
            required_checks=[],
            policy={"note": SECRET},
            payment_trigger=opts.get("trigger", "merged_and_alert_fixed"),
            amount_ate=opts.get("amount", 100),
            fee_bps=0,
            required_kya=opts.get("kya", 1),
            required_ema=opts.get("ema", 0.5),
            security_tier=bool(opts.get("security", False)),
            created_at=WHEN,
        )
    )
    if opts.get("dispatch"):
        db.sync.add(
            GitHubAlertDispatch(
                id=uuid.uuid4(),
                repo_full_name=repo,
                alert_kind=opts["dispatch"],
                alert_number=opts["issue"],
                issue_number=opts["issue"],
                amount_ate=opts.get("amount", 100),
                policy_overrides={"note": SECRET},
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
    for lease in opts.get("leases", ()):
        db.sync.add(
            GitHubLease(
                id=uuid.uuid4(),
                binding_id=binding_id,
                agent_id=SECRET,
                user_id=uuid.uuid4(),
                github_user_id=7,
                spec_id=spec_id,
                token_hash=f"{SECRET}-{title}-{lease['status']}",
                status=lease["status"],
                work_expires_at=lease["expires"],
                hard_expires_at=lease["expires"],
                hold_escrow_id=lease.get("hold"),
                hold_cancel_secret=SECRET,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
    settlement = opts.get("settlement")
    if settlement:
        db.sync.add(
            GitHubSettlement(
                id=uuid.uuid4(),
                binding_id=binding_id,
                spec_id=spec_id,
                solver_agent_id=SECRET,
                head_sha=SECRET,
                state=settlement,
                escrow_id=SECRET,
                cancel_secret=SECRET,
                idempotency_prefix=SECRET,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
    return str(bounty_id)


@pytest.fixture
def world():
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    bridge.sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=4242,
            account_login=SECRET,
        )
    )
    for repo, visibility in {
        "acme/cyber": "public",
        "acme/private": "private",
        "acme/internal": "internal",
        "acme/unknown": None,
        "acme/capital": "Public",
    }.items():
        bridge.sync.add(
            GitHubRepoLink(
                id=uuid.uuid4(),
                installation_id=4242,
                repo_full_name=repo,
                funder_user_id=uuid.uuid4(),
                enabled=True,
                default_policy={"note": SECRET},
                required_checks=[],
                visibility=visibility,
            )
        )
    ids = {
        "passed": _add(
            bridge,
            repo="acme/cyber",
            title="passed",
            issue=1,
            dispatch="dependabot",
            state="needs_review",
            pr=15,
            amount=150,
            kya=2,
            ema=0.85,
            trigger="merge_with_checks",
            evaluated_head_sha=HEAD,
            last_assessment={
                "outcome": "ACCEPTABLE",
                "head_sha": HEAD,
                "findings": [SECRET],
            },
            leases=({"status": "submitted", "expires": WHEN, "hold": "hold-real"},),
        ),
        "needs": _add(
            bridge,
            repo="acme/cyber",
            title="needs",
            issue=7,
            dispatch="dependabot",
            state="needs_review",
            evaluated_head_sha=HEAD,
            last_assessment={"outcome": " NEEDS_REVIEW "},
        ),
        "failed": _add(
            bridge,
            repo="acme/cyber",
            title="failed",
            issue=2,
            security=True,
            state="advertised",
            last_assessment={"outcome": "REJECTED_CHECKS", "head_sha": FAILED_HEAD, "findings": [SECRET]},
        ),
        "pending": _add(
            bridge,
            repo="acme/cyber",
            title="pending",
            issue=3,
            dispatch="code_scanning",
            state="evaluating",
            last_assessment={"findings": [SECRET]},
        ),
        "paid": _add(
            bridge,
            repo="acme/cyber",
            title="paid",
            issue=4,
            dispatch="dependabot",
            state="paid",
            bounty_status="COMPLETED",
            settlement="released",
            last_assessment={"findings": [SECRET]},
        ),
        "cancelled": _add(
            bridge,
            repo="acme/cyber",
            title="cancelled",
            issue=5,
            dispatch="dependabot",
            state="cancelled",
        ),
        "private": _add(
            bridge,
            repo="acme/private",
            title="private-hidden",
            issue=1,
            dispatch="dependabot",
            security=True,
        ),
        "internal": _add(
            bridge,
            repo="acme/internal",
            title="internal-hidden",
            issue=1,
            dispatch="dependabot",
        ),
        "unknown_vis": _add(
            bridge,
            repo="acme/unknown",
            title="unknown-hidden",
            issue=1,
            dispatch="dependabot",
        ),
        "capital": _add(
            bridge,
            repo="acme/capital",
            title="capital-hidden",
            issue=1,
            dispatch="dependabot",
        ),
        "plain": _add(
            bridge,
            repo="acme/cyber",
            title="plain-hidden",
            issue=6,
        ),
        "missing": _add(
            bridge,
            repo="acme/missing",
            title="missing-hidden",
            issue=1,
            dispatch="dependabot",
        ),
    }
    bridge.sync.commit()
    client = _client_for(bridge)
    try:
        yield {"client": client, "bridge": bridge, "ids": ids}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _get(world, bounty_id: str):
    return world["client"].get(f"/api/v1/cyber/tasks/{bounty_id}")


def test_detail_sql_requires_a_public_cyber_task():
    statement = cyber_tasks._detail_statement(uuid.uuid4())
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    assert "github_alert_dispatches" in sql
    assert "security_tier" in sql
    assert "github_repo_links" in sql
    assert "visibility" in sql


def test_public_detail_shape_and_check_facts(world):
    passed = _get(world, world["ids"]["passed"])
    assert passed.status_code == 200, passed.text
    body = passed.json()
    checked = CyberTaskDetailOut.model_validate(body)
    assert set(body) == set(CyberTaskDetailOut.model_fields)
    assert set(body["checks"]) == set(ChecksResult.model_fields)
    assert checked.bounty_id == world["ids"]["passed"]
    assert body["title"] == "passed"
    assert body["repo"] == "acme/cyber"
    assert body["issue_number"] == 1
    assert body["issue_url"] == "https://github.com/acme/cyber/issues/1"
    assert body["pr_number"] == 15
    assert body["pr_url"] == "https://github.com/acme/cyber/pull/15"
    assert body["source"] == "dependabot"
    assert body["reward_ate"] == 150
    assert body["funding_state"] == "funds_held"
    assert body["work_state"] == "needs_maintainer_review"
    assert body["payment_trigger"] == "merge_with_checks"
    assert body["eligibility"] == {"min_reputation": 0.85, "min_kya": 2}
    assert body["checks"] == {"state": "passed", "head_sha": HEAD}
    assert body["evaluation"] == "acceptable"
    assert datetime.fromisoformat(body["work_expires_at"]) == WHEN
    assert body["challenge_id"] is None
    assert body["challenge_verdict"] is None
    assert body["open_findings"] == 0
    assert SECRET not in passed.text

    failed = _get(world, world["ids"]["failed"]).json()
    assert failed["source"] == "security_label"
    assert failed["work_state"] == "available"
    assert failed["checks"] == {"state": "failed", "head_sha": FAILED_HEAD}
    assert failed["evaluation"] is None
    assert failed["work_expires_at"] is None
    assert failed["open_findings"] == 0
    assert failed["challenge_verdict"] is None

    needs = _get(world, world["ids"]["needs"]).json()
    assert needs["evaluation"] == "needs_review"
    assert needs["checks"] == {"state": "passed", "head_sha": HEAD}
    assert needs["open_findings"] == 0

    pending = _get(world, world["ids"]["pending"]).json()
    assert pending["source"] == "code_scanning"
    assert pending["work_state"] == "evaluating"
    assert pending["checks"] == {"state": "pending", "head_sha": None}
    assert pending["evaluation"] is None

    paid = _get(world, world["ids"]["paid"])
    assert paid.status_code == 200, paid.text
    paid_body = paid.json()
    assert paid_body["work_state"] == "completed"
    assert paid_body["funding_state"] == "paid"
    assert paid_body["checks"] == {"state": "not_run", "head_sha": None}
    assert paid_body["evaluation"] is None
    assert SECRET not in paid.text

    cancelled = _get(world, world["ids"]["cancelled"])
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["work_state"] == "cancelled"
    assert cancelled.json()["funding_state"] == "not_paid"
    assert SECRET not in cancelled.text


def test_unknown_and_non_public_share_one_404(world):
    hidden = [
        "not-a-uuid",
        str(uuid.uuid4()),
        world["ids"]["private"],
        world["ids"]["internal"],
        world["ids"]["unknown_vis"],
        world["ids"]["capital"],
        world["ids"]["plain"],
        world["ids"]["missing"],
    ]
    bodies = []
    for bounty_id in hidden:
        response = _get(world, bounty_id)
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
        bodies.append(response.json())
        assert SECRET not in response.text
        assert "private-hidden" not in response.text
    assert bodies == [NOT_FOUND] * len(hidden)
    again = _get(world, hidden[0])
    assert again.json() == _get(world, world["ids"]["private"]).json()


def test_detail_reads_do_not_mutate_rows(world):
    before = _snapshot(world["bridge"].sync)
    for bounty_id in [world["ids"]["passed"], world["ids"]["private"], "not-a-uuid", str(uuid.uuid4())]:
        _get(world, bounty_id)
        _get(world, bounty_id)
    after = _snapshot(world["bridge"].sync)
    assert before == after
