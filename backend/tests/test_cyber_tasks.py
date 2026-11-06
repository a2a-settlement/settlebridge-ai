"""SettleBridge Cyber task list. Clauses compile to PostgreSQL; rows use a slim bounties table.

The Bounty model uses Postgres ARRAY and Enum columns, so these tests never call
Bounty.__table__.create. The stand-in table has only the columns the listing reads.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.models.bounty import Bounty
from app.models.github import (
    AcceptanceSpec,
    GitHubAlertDispatch,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
)
from app.routes.cyber import router
from app.services import cyber_tasks, exchange, github_app
from app.services import github_bounty_service as bounty_svc
from app.services.github_bounty_service import build_spec_content, handle_job, spec_content_hash
from app.services.repo_visibility import bounty_public_clause

REPO = "acme/widget"
FUNDER = uuid.UUID("11111111-1111-4111-a111-111111111111")
SOLVER = uuid.UUID("22222222-2222-4222-b222-222222222222")
SECRETS = (
    "SECRET_FINDING_LEAK",
    "SECRET_POLICY_LEAK",
    "SECRET_TEST_COMMAND",
    "SECRET_HOLD_ESCROW",
    "SECRET_HOLD_CANCEL",
    "SECRET_LEASE_TOKEN",
    "SECRET_SETTLEMENT_CANCEL",
    "SECRET_ESCROW_ID",
    "SECRET_FUNDER_LOGIN",
    "SECRET_SOLVER_AGENT",
    "SECRET_IDEMPOTENCY",
    "SECRET_HEAD_SHA",
    "11111111-1111-4111-a111-111111111111",
    "22222222-2222-4222-b222-222222222222",
    "pending:15",
)
REPOS = {
    "acme/cyber": "public",
    "acme/private": "private",
    "acme/unknown": None,
    "acme/internal": "internal",
    "acme/capital": "Public",
}
ITEM_KEYS = {
    "bounty_id",
    "title",
    "repo",
    "issue_number",
    "issue_url",
    "pr_number",
    "pr_url",
    "source",
    "reward_ate",
    "funding_state",
    "work_state",
    "payment_trigger",
    "eligibility",
    "created_at",
    "updated_at",
}
FORBIDDEN_KEYS = {
    "description",
    "body",
    "issue_body",
    "last_assessment",
    "findings",
    "escrow_id",
    "hold_escrow_id",
    "lease_id",
    "token",
    "token_hash",
    "cancel_secret",
    "hold_cancel_secret",
    "funder",
    "funder_user_id",
    "requester",
    "requester_id",
    "policy",
    "test_command",
    "content_hash",
    "spec_id",
    "binding_id",
    "user_id",
    "agent_id",
    "github_user_id",
    "idempotency_prefix",
    "head_sha",
    "comment_id",
    "fee_bps",
    "security_tier",
    "alert_kind",
    "alert_number",
}
AVAILABLE = {
    "disp",
    "label",
    "page_a",
    "page_b",
    "page_c",
    "expired_lease",
    "newer_kind",
}
IN_PROGRESS = {
    "claimed",
    "submitted",
    "pending_hold",
    "evaluating",
    "review",
    "merge",
    "settling",
    "failed_settle",
}
COMPLETED = {"paid", "label_paid", "paid_leased"}
HIDDEN = {
    "plain",
    "not_open",
    "cancelled",
    "private",
    "unknown",
    "internal",
    "missing",
    "capital",
}


def _task(key, repo, **kwargs):
    row = {"key": key, "repo": repo}
    row.update(kwargs)
    return row


TASKS = [
    _task("disp", "acme/cyber", dispatch="dependabot", day=1, amount=100, kya=1, ema=0.5),
    _task(
        "label",
        "acme/cyber",
        security=True,
        day=2,
        amount=200,
        kya=2,
        ema=0.85,
        trigger="merge_with_checks",
    ),
    _task("plain", "acme/cyber", day=3, title="security dependabot CVE fix"),
    _task("page_a", "acme/cyber", dispatch="dependabot", day=4),
    _task("page_b", "acme/cyber", dispatch="dependabot", day=5),
    _task("page_c", "acme/cyber", dispatch="dependabot", day=6),
    _task(
        "expired_lease",
        "acme/cyber",
        dispatch="dependabot",
        day=7,
        lease="expired",
        hold="SECRET_HOLD_ESCROW",
    ),
    _task(
        "newer_kind",
        "acme/cyber",
        dispatch="code_scanning",
        extra_dispatch="dependabot",
        day=8,
    ),
    _task("not_open", "acme/cyber", dispatch="dependabot", day=9, bounty_status="CLAIMED"),
    _task(
        "claimed",
        "acme/cyber",
        dispatch="code_scanning",
        day=10,
        lease="work",
        hold="SECRET_HOLD_ESCROW",
        pr=8,
    ),
    _task(
        "submitted",
        "acme/cyber",
        dispatch="dependabot",
        day=11,
        lease="submitted",
        hold="pending:15",
        pr=9,
    ),
    _task(
        "pending_hold",
        "acme/cyber",
        dispatch="dependabot",
        day=12,
        lease="work",
        hold="pending",
    ),
    _task("evaluating", "acme/cyber", dispatch="dependabot", day=13, state="evaluating"),
    _task(
        "review",
        "acme/cyber",
        dispatch="dependabot",
        day=14,
        state="needs_review",
        settlement="needs_review",
    ),
    _task(
        "merge",
        "acme/cyber",
        dispatch="dependabot",
        day=15,
        state="awaiting_merge",
        pr=11,
    ),
    _task(
        "settling",
        "acme/cyber",
        dispatch="dependabot",
        day=16,
        state="settlement_pending",
        settlement="pending",
    ),
    _task(
        "failed_settle",
        "acme/cyber",
        dispatch="dependabot",
        day=17,
        state="settlement_pending",
        settlement="failed",
    ),
    _task("paid", "acme/cyber", dispatch="dependabot", day=18, state="paid", settlement="released"),
    _task(
        "label_paid",
        "acme/cyber",
        security=True,
        day=19,
        state="paid",
        bounty_status="COMPLETED",
        trigger="merge_with_checks",
        kya=2,
        ema=0.85,
    ),
    _task(
        "paid_leased",
        "acme/cyber",
        dispatch="dependabot",
        day=20,
        state="paid",
        settlement="released",
        lease="work",
        hold="SECRET_HOLD_ESCROW",
    ),
    _task("cancelled", "acme/cyber", dispatch="dependabot", day=21, state="cancelled"),
    _task("private", "acme/private", dispatch="dependabot", security=True, day=1),
    _task("unknown", "acme/unknown", dispatch="dependabot", day=1),
    _task("internal", "acme/internal", dispatch="dependabot", day=1),
    _task("missing", "acme/missing", dispatch="dependabot", day=1),
    _task("capital", "acme/capital", dispatch="dependabot", day=1),
]


def _stamp(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc)


def _sql(stmt, *, literal: bool = False) -> str:
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": literal},
    )
    return " ".join(str(compiled).split())


def _lease(status, hold):
    return SimpleNamespace(status=status, hold_escrow_id=hold)


def _binding(state):
    return SimpleNamespace(state=state)


def _settlement(state):
    if state is None:
        return None
    return SimpleNamespace(state=state)


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync
        self.queries = 0

    async def execute(self, statement, *args, **kwargs):
        self.queries += 1
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


def _insert_bounty(db: _Bridge, bounty_id, title: str, status: str) -> None:
    db.sync.execute(
        text("INSERT INTO bounties (id, title, status) VALUES (:id, :title, :status)"),
        {"id": bounty_id.hex, "title": title, "status": status},
    )


def _seed(db: _Bridge) -> None:
    db.sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=4242,
            account_login="SECRET_FUNDER_LOGIN",
        )
    )
    for repo, visibility in REPOS.items():
        db.sync.add(
            GitHubRepoLink(
                id=uuid.uuid4(),
                installation_id=4242,
                repo_full_name=repo,
                funder_user_id=FUNDER,
                enabled=True,
                default_policy={},
                required_checks=[],
                visibility=visibility,
            )
        )
    issues: dict[str, int] = {}
    alert_number = 0
    for task in TASKS:
        repo = task["repo"]
        issues[repo] = issues.get(repo, 0) + 1
        issue_number = issues[repo]
        bounty_id = uuid.uuid4()
        binding_id = uuid.uuid4()
        spec_id = uuid.uuid4()
        when = _stamp(task.get("day", 1))
        _insert_bounty(
            db,
            bounty_id,
            task.get("title", task["key"]),
            task.get("bounty_status", "OPEN"),
        )
        db.sync.add(
            GitHubBountyBinding(
                id=binding_id,
                bounty_id=bounty_id,
                repo_full_name=repo,
                issue_number=issue_number,
                current_spec_id=spec_id,
                pr_number=task.get("pr"),
                state=task.get("state", "advertised"),
                last_assessment={"findings": ["SECRET_FINDING_LEAK"]},
                created_at=when,
                updated_at=when,
            )
        )
        db.sync.add(
            AcceptanceSpec(
                id=spec_id,
                binding_id=binding_id,
                version=1,
                content_hash=f"hash-{task['key']}",
                test_command="SECRET_TEST_COMMAND",
                required_checks=[],
                policy={"note": "SECRET_POLICY_LEAK"},
                payment_trigger=task.get("trigger", "merged_and_alert_fixed"),
                amount_ate=task.get("amount", 100),
                fee_bps=0,
                required_kya=task.get("kya", 1),
                required_ema=task.get("ema", 0.5),
                security_tier=bool(task.get("security", False)),
                created_at=when,
            )
        )
        kind = task.get("dispatch")
        if kind:
            alert_number += 1
            db.sync.add(
                GitHubAlertDispatch(
                    id=uuid.uuid4(),
                    repo_full_name=repo,
                    alert_kind=kind,
                    alert_number=alert_number,
                    issue_number=issue_number,
                    amount_ate=task.get("amount", 100),
                    policy_overrides={"note": "SECRET_POLICY_LEAK"},
                    created_at=when,
                    updated_at=when,
                )
            )
        extra = task.get("extra_dispatch")
        if extra:
            alert_number += 1
            db.sync.add(
                GitHubAlertDispatch(
                    id=uuid.uuid4(),
                    repo_full_name=repo,
                    alert_kind=extra,
                    alert_number=alert_number,
                    issue_number=issue_number,
                    amount_ate=task.get("amount", 100),
                    policy_overrides={},
                    created_at=_stamp(1),
                    updated_at=_stamp(1),
                )
            )
        lease_status = task.get("lease")
        if lease_status:
            db.sync.add(
                GitHubLease(
                    id=uuid.uuid4(),
                    binding_id=binding_id,
                    agent_id="SECRET_SOLVER_AGENT",
                    user_id=SOLVER,
                    github_user_id=7,
                    spec_id=spec_id,
                    token_hash=f"SECRET_LEASE_TOKEN_{task['key']}",
                    status=lease_status,
                    work_expires_at=when,
                    hard_expires_at=when,
                    hold_escrow_id=task.get("hold"),
                    hold_cancel_secret="SECRET_HOLD_CANCEL",
                    created_at=when,
                    updated_at=when,
                )
            )
        settlement_state = task.get("settlement")
        if settlement_state:
            db.sync.add(
                GitHubSettlement(
                    id=uuid.uuid4(),
                    binding_id=binding_id,
                    spec_id=spec_id,
                    solver_agent_id="SECRET_SOLVER_AGENT",
                    head_sha="SECRET_HEAD_SHA",
                    state=settlement_state,
                    escrow_id="SECRET_ESCROW_ID",
                    cancel_secret="SECRET_SETTLEMENT_CANCEL",
                    idempotency_prefix=f"SECRET_IDEMPOTENCY_{task['key']}",
                    created_at=when,
                    updated_at=when,
                )
            )
    alert_number += 1
    db.sync.add(
        GitHubAlertDispatch(
            id=uuid.uuid4(),
            repo_full_name="acme/cyber",
            alert_kind="dependabot",
            alert_number=alert_number,
            issue_number=99999,
            amount_ate=1,
            policy_overrides={},
            created_at=_stamp(1),
            updated_at=_stamp(1),
        )
    )
    db.sync.commit()


def _client_for(bridge: _Bridge) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


@pytest.fixture
def world():
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    _seed(bridge)
    client = _client_for(bridge)
    try:
        yield {"client": client, "bridge": bridge}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


@pytest.fixture
def empty():
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    client = _client_for(bridge)
    try:
        yield {"client": client, "bridge": bridge}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _get(world, view: str, **params):
    world["bridge"].queries = 0
    response = world["client"].get("/api/v1/cyber/tasks", params={"view": view, **params})
    assert response.status_code == 200, response.text
    return response.json(), response.text


def _titles(payload) -> list[str]:
    return [item["title"] for item in payload["items"]]


def _keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


@pytest.mark.parametrize(
    ("binding_state", "settlement_state", "leases", "expected"),
    [
        ("paid", None, (), "paid"),
        ("paid", "released", (), "paid"),
        ("paid", "failed", (), "not_paid"),
        ("paid", "needs_review", (), "payment_review"),
        ("paid", "pending", (), "payment_pending"),
        ("advertised", "failed", (), "not_paid"),
        ("advertised", "refunded", (), "not_paid"),
        ("cancelled", "released", (("work", "hold-1"),), "not_paid"),
        ("cancelled", None, (), "not_paid"),
        ("advertised", "needs_review", (), "payment_review"),
        ("advertised", "pending", (), "payment_pending"),
        ("advertised", "escrow_created", (), "payment_pending"),
        ("advertised", "delivered", (), "payment_pending"),
        ("settlement_pending", None, (), "payment_pending"),
        ("settlement_pending", "needs_review", (), "payment_review"),
        ("settlement_pending", "failed", (), "not_paid"),
        ("settlement_pending", "released", (), "payment_pending"),
        ("advertised", "released", (), "balance_checked"),
        ("advertised", None, (("work", "hold-1"),), "funds_held"),
        ("advertised", None, (("submitted", "hold-1"),), "funds_held"),
        ("advertised", None, (("work", "pending"),), "balance_checked"),
        ("advertised", None, (("work", "pending:30"),), "balance_checked"),
        ("advertised", None, (("work", None),), "balance_checked"),
        ("advertised", None, (("work", ""),), "balance_checked"),
        ("advertised", None, (("expired", "hold-1"),), "balance_checked"),
        ("advertised", None, (("released", "hold-1"),), "balance_checked"),
        ("advertised", None, (), "balance_checked"),
        ("advertised", "pending", (("work", "hold-1"),), "payment_pending"),
        ("advertised", None, (("work", "pending"), ("submitted", "hold-1")), "funds_held"),
    ],
)
def test_funding_state_table(binding_state, settlement_state, leases, expected):
    assert (
        cyber_tasks.funding_state(
            _binding(binding_state),
            _settlement(settlement_state),
            tuple(_lease(status, hold) for status, hold in leases),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("binding_state", "lease_status", "expected"),
    [
        ("cancelled", "work", "cancelled"),
        ("paid", "submitted", "completed"),
        ("settlement_pending", "work", "settling"),
        ("awaiting_merge", "submitted", "awaiting_merge"),
        ("needs_review", "work", "needs_maintainer_review"),
        ("evaluating", "submitted", "evaluating"),
        ("advertised", "submitted", "pr_submitted"),
        ("advertised", "work", "claimed"),
        ("advertised", None, "available"),
        ("advertised", "expired", "available"),
        ("advertised", "released", "available"),
        ("advertised", "settled", "available"),
        ("evaluating", None, "evaluating"),
    ],
)
def test_work_state_table(binding_state, lease_status, expected):
    lease = None if lease_status is None else _lease(lease_status, None)
    assert cyber_tasks.work_state(_binding(binding_state), lease) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (None, "security_label"),
        ("dependabot", "dependabot"),
        ("code_scanning", "code_scanning"),
        ("secret_scanning", "security_label"),
    ],
)
def test_task_source(kind, expected):
    dispatch = None if kind is None else SimpleNamespace(alert_kind=kind)
    assert cyber_tasks.task_source(dispatch) == expected


def test_newer_dispatch_wins_the_source():
    older = SimpleNamespace(
        repo_full_name="acme/cyber",
        issue_number=4,
        alert_kind="dependabot",
        updated_at=_stamp(1),
        id="a",
    )
    newer = SimpleNamespace(
        repo_full_name="acme/cyber",
        issue_number=4,
        alert_kind="code_scanning",
        updated_at=_stamp(8),
        id="b",
    )
    other = SimpleNamespace(
        repo_full_name="acme/cyber",
        issue_number=5,
        alert_kind="dependabot",
        updated_at=_stamp(9),
        id="c",
    )
    chosen = cyber_tasks._index_dispatches([older, other, newer], {("acme/cyber", 4)})
    assert cyber_tasks.task_source(chosen[("acme/cyber", 4)]) == "code_scanning"
    assert set(chosen) == {("acme/cyber", 4)}


def test_listing_sql_requires_a_public_classified_repo():
    available = _sql(cyber_tasks._page_statement("available", 1, 12), literal=True)
    progress = _sql(cyber_tasks._page_statement("in_progress", 1, 12), literal=True)
    completed = _sql(cyber_tasks._page_statement("completed", 1, 12), literal=True)
    for sql in (available, progress, completed):
        assert "github_alert_dispatches" in sql
        assert "security_tier" in sql
        assert "github_repo_links" in sql
        assert "visibility" in sql
        assert "funder_user_id" not in sql
        assert "LIKE" not in sql
    assert "advertised" in available
    assert "github_leases" in available
    assert "evaluating" in progress
    assert "needs_review" in progress
    assert "awaiting_merge" in progress
    assert "settlement_pending" in progress
    assert "cancelled" in progress
    assert "paid" in completed
    public = _sql(select(Bounty.id).where(bounty_public_clause()), literal=True)
    assert "visibility = 'public'" in public
    assert "github_bounty_bindings.bounty_id = bounties.id" in public


def test_classification_ignores_titles_and_hides_non_public_repos(world):
    listed = {}
    for view in ("available", "in_progress", "completed"):
        payload, _body = _get(world, view, page_size=50)
        listed[view] = set(_titles(payload))
    assert listed["available"] == AVAILABLE
    assert listed["in_progress"] == IN_PROGRESS
    assert listed["completed"] == COMPLETED
    shown = set().union(*listed.values())
    assert shown.isdisjoint(HIDDEN)
    assert "security dependabot CVE fix" not in shown


def test_view_state_mapping_is_wired(world):
    found = {}
    for view in ("available", "in_progress", "completed"):
        payload, _body = _get(world, view, page_size=50)
        for item in payload["items"]:
            found[item["title"]] = item
    expect = {
        "disp": ("balance_checked", "available", "dependabot"),
        "label": ("balance_checked", "available", "security_label"),
        "newer_kind": ("balance_checked", "available", "code_scanning"),
        "expired_lease": ("balance_checked", "available", "dependabot"),
        "claimed": ("funds_held", "claimed", "code_scanning"),
        "submitted": ("balance_checked", "pr_submitted", "dependabot"),
        "pending_hold": ("balance_checked", "claimed", "dependabot"),
        "evaluating": ("balance_checked", "evaluating", "dependabot"),
        "review": ("payment_review", "needs_maintainer_review", "dependabot"),
        "merge": ("balance_checked", "awaiting_merge", "dependabot"),
        "settling": ("payment_pending", "settling", "dependabot"),
        "failed_settle": ("not_paid", "settling", "dependabot"),
        "paid": ("paid", "completed", "dependabot"),
        "label_paid": ("paid", "completed", "security_label"),
        "paid_leased": ("paid", "completed", "dependabot"),
    }
    for title, (funding, work, source) in expect.items():
        item = found[title]
        assert item["funding_state"] == funding
        assert item["work_state"] == work
        assert item["source"] == source


def test_response_contract_omits_private_fields(world):
    bodies = []
    items = {}
    for view in ("available", "in_progress", "completed"):
        payload, body = _get(world, view, page_size=50)
        bodies.append(body)
        assert payload["view"] == view
        assert payload["page"] == 1
        assert payload["page_size"] == 50
        assert payload["total"] == len(payload["items"])
        assert set(payload) == {"view", "page", "page_size", "total", "items"}
        for item in payload["items"]:
            items[item["title"]] = item
            assert set(item) == ITEM_KEYS
            assert set(item["eligibility"]) == {"min_reputation", "min_kya"}
            repo = item["repo"]
            number = item["issue_number"]
            assert item["issue_url"] == f"https://github.com/{repo}/issues/{number}"
            if item["pr_number"] is None:
                assert item["pr_url"] is None
            else:
                assert item["pr_url"] == f"https://github.com/{repo}/pull/{item['pr_number']}"
            assert isinstance(item["reward_ate"], int)
            assert isinstance(item["issue_number"], int)
            assert isinstance(item["eligibility"]["min_kya"], int)
            assert isinstance(item["eligibility"]["min_reputation"], float)
            found: set[str] = set()
            _keys(item, found)
            assert found.isdisjoint(FORBIDDEN_KEYS)
    text_body = "\n".join(bodies)
    for secret in SECRETS:
        assert secret not in text_body
    disp = items["disp"]
    assert disp["repo"] == "acme/cyber"
    assert disp["pr_number"] is None
    assert disp["reward_ate"] == 100
    assert disp["payment_trigger"] == "merged_and_alert_fixed"
    assert disp["eligibility"] == {"min_reputation": 0.5, "min_kya": 1}
    assert disp["created_at"].startswith("2026-01-01")
    assert disp["updated_at"].startswith("2026-01-01")
    label = items["label"]
    assert label["source"] == "security_label"
    assert label["reward_ate"] == 200
    assert label["payment_trigger"] == "merge_with_checks"
    assert label["eligibility"] == {"min_reputation": 0.85, "min_kya": 2}
    claimed = items["claimed"]
    assert claimed["pr_number"] == 8
    assert claimed["pr_url"] == "https://github.com/acme/cyber/pull/8"
    assert claimed["payment_trigger"] == "merged_and_alert_fixed"


def test_pagination_total_and_newest_first(world):
    payload, _body = _get(world, "available")
    assert payload["page"] == 1
    assert payload["page_size"] == 12
    assert payload["total"] == len(AVAILABLE)
    assert _titles(payload) == [
        "newer_kind",
        "expired_lease",
        "page_c",
        "page_b",
        "page_a",
        "label",
        "disp",
    ]
    pages = []
    for page in (1, 2, 3, 4):
        payload, _body = _get(world, "available", page=page, page_size=2)
        assert payload["page"] == page
        assert payload["page_size"] == 2
        assert payload["total"] == len(AVAILABLE)
        pages.append(_titles(payload))
    assert pages == [
        ["newer_kind", "expired_lease"],
        ["page_c", "page_b"],
        ["page_a", "label"],
        ["disp"],
    ]


def test_queries_stay_batched(world, empty):
    _get(world, "available", page_size=50)
    assert world["bridge"].queries == 5
    _get(world, "available", page=1, page_size=2)
    assert world["bridge"].queries == 5
    _get(world, "in_progress", page_size=50)
    assert world["bridge"].queries == 5
    payload, _body = _get(empty, "completed")
    assert payload["total"] == 0
    assert payload["items"] == []
    assert empty["bridge"].queries == 1


@pytest.mark.parametrize(
    "params",
    [
        {"view": "nope"},
        {"view": "AVAILABLE"},
        {"view": "cancelled"},
        {"view": "available", "page": 0},
        {"view": "available", "page_size": 0},
        {"view": "available", "page_size": 51},
    ],
)
def test_invalid_query_is_422(world, params):
    response = world["client"].get("/api/v1/cyber/tasks", params=params)
    assert response.status_code == 422


def test_view_defaults_to_available(world):
    response = world["client"].get("/api/v1/cyber/tasks")
    assert response.status_code == 200
    assert response.json()["view"] == "available"


@pytest.mark.parametrize(
    ("labels", "security_tier", "required_kya", "required_ema"),
    [
        ([{"name": "bounty: $25"}, {"name": "tier: security"}], True, 2, 0.85),
        ([{"name": "bounty: $25"}], False, 1, 0.75),
    ],
)
def test_freeze_sets_security_tier_from_the_security_label(
    monkeypatch, labels, security_tier, required_kya, required_ema
):
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="bot-1")
    link = SimpleNamespace(
        id=uuid.uuid4(),
        installation_id=42,
        repo_full_name=REPO,
        funder_user_id=user.id,
        enabled=True,
        default_policy={"deny_paths": ["secrets/"]},
        required_checks=[{"name": "ci", "app_id": 7}],
        visibility="public",
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(side_effect=AssertionError("handler issued a query"))
    monkeypatch.setattr(bounty_svc, "get_binding_by_issue", AsyncMock(return_value=None))
    monkeypatch.setattr(bounty_svc, "get_enabled_funded_repo_link", AsyncMock(return_value=link))
    monkeypatch.setattr(bounty_svc, "lock_user", AsyncMock(return_value=user))
    monkeypatch.setattr(bounty_svc, "sum_advertised_cost", AsyncMock(return_value=0))
    monkeypatch.setattr(
        exchange, "get_balance_dashboard", MagicMock(return_value={"available": 1_000_000})
    )
    monkeypatch.setattr(github_app, "upsert_sticky_comment", AsyncMock(return_value=77))

    async def _no_github(*_args, **_kwargs):
        raise AssertionError("unexpected GitHub read")

    monkeypatch.setattr(github_app, "get_repository", _no_github)
    job = GitHubJob(
        kind=JobKind.ISSUE_LABELED.value,
        payload={
            "event": "issues",
            "action": "labeled",
            "data": {
                "installation": {"id": 42},
                "repository": {"full_name": REPO},
                "issue": {
                    "number": 12,
                    "title": "Fix the widget",
                    "body": "Please reproduce\n```\npytest -q\n```\n",
                    "labels": labels,
                },
            },
        },
    )
    asyncio.run(handle_job(db, job))
    specs = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AcceptanceSpec)]
    assert len(specs) == 1
    spec = specs[0]
    assert spec.security_tier is security_tier
    assert spec.required_kya == required_kya
    assert spec.required_ema == required_ema
    assert spec.payment_trigger == "merge_with_checks"
    assert spec.content_hash == spec_content_hash(
        build_spec_content(
            test_command="pytest -q",
            required_checks=[{"name": "ci", "app_id": 7}],
            policy={"deny_paths": ["secrets/"]},
            payment_trigger="merge_with_checks",
            amount_ate=2500,
            fee_bps=settings.GITHUB_CLEARING_FEE_BPS,
            required_kya=required_kya,
            required_ema=required_ema,
        )
    )
