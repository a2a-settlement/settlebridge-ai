"""GET /api/v1/github/leases/mine. SQLite rows through the async bridge. No network."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import (
    AcceptanceSpec,
    GitHubBountyBinding,
    GitHubLease,
    GitHubSettlement,
)
from app.routes.github_my_leases import router

URL = "/api/v1/github/leases/mine"
ME = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ITEM_KEYS = {
    "lease_id",
    "bounty_id",
    "repo",
    "issue_number",
    "status",
    "work_expires_at",
    "pr_number",
    "evaluation",
    "payout",
    "amount_ate",
}
SECRET_MARKERS = (
    "secret-lease-token",
    "secret-hold-cancel",
    "secret-hold-escrow",
    "secret-settle-cancel",
    "secret-settle-escrow",
    "secret-finding",
    "secret-solver",
    "secret-head",
    "secret-idem",
    "secret-test-command",
    "secret-content-hash",
    "secret-diff",
    "secret-spec",
)


def _lease_uuid(n: int) -> uuid.UUID:
    # Include a hex letter. SQLite gives an undeclared UUID column numeric
    # affinity, and an all-digit hex string collapses to one real.
    return uuid.UUID(f"11111111-1111-4111-a111-{n:012d}")


def _created(second: int) -> datetime:
    return datetime(2026, 4, 2, 12, 0, second, tzinfo=timezone.utc)


def _expires(second: int) -> datetime:
    return datetime(2026, 5, 1, 8, 0, second, tzinfo=timezone.utc)


def _expires_iso(second: int) -> str:
    return f"2026-05-01T08:00:{second:02d}Z"


def _row(
    n: int,
    second: int,
    *,
    user_id: uuid.UUID = ME,
    status: str = "work",
    assessment=None,
    binding_state: str = "advertised",
    settlement: str | None = None,
    lease_pr: int | None = None,
    binding_pr: int | None = None,
    amount: int = 100,
    current_amount: int | None = None,
    repo: str = "acme/widget",
    evaluation: str | None = None,
    payout: str | None = None,
    pr_number: int | None = None,
) -> dict:
    lease_id = _lease_uuid(n)
    return {
        "lease_id": lease_id,
        "user_id": user_id,
        "created_at": _created(second),
        "work_expires_at": _expires(second),
        "status": status,
        "assessment": assessment,
        "binding_state": binding_state,
        "settlement": settlement,
        "lease_pr": lease_pr,
        "binding_pr": binding_pr,
        "amount": amount,
        "current_amount": current_amount,
        "repo": repo,
        "issue": n,
        "public": {
            "lease_id": str(lease_id),
            "repo": repo,
            "issue_number": n,
            "status": status,
            "work_expires_at": _expires_iso(second),
            "pr_number": pr_number,
            "evaluation": evaluation,
            "payout": payout,
            "amount_ate": amount,
        },
    }


def _rows() -> list[dict]:
    # second is the age rank. Same second is ordered by lease id descending.
    acceptable = {
        "outcome": "ACCEPTABLE",
        "spec_id": "secret-spec",
        "diff_sha256": "secret-diff",
        "findings": [{"msg": "secret-finding"}],
    }
    return [
        _row(
            1,
            50,
            status="submitted",
            assessment=acceptable,
            binding_state="awaiting_merge",
            lease_pr=11,
            binding_pr=99,
            amount=250,
            current_amount=9999,
            repo="octo/hello",
            evaluation="acceptable",
            pr_number=11,
        ),
        _row(
            2,
            49,
            status="settled",
            assessment={"outcome": "NEEDS_REVIEW", "findings": [{"msg": "secret-finding"}]},
            binding_state="needs_review",
            settlement="pending",
            binding_pr=4,
            amount=80,
            evaluation="needs_review",
            payout="pending",
            pr_number=4,
        ),
        _row(
            3,
            48,
            status="settled",
            assessment={"outcome": "REJECTED_CHECKS"},
            binding_state="paid",
            settlement="released",
            lease_pr=7,
            binding_pr=8,
            amount=500,
            evaluation=None,
            payout="paid",
            pr_number=7,
        ),
        _row(
            4,
            47,
            status="settled",
            assessment=None,
            binding_state="advertised",
            settlement="failed",
            amount=10,
            payout="not_paid",
        ),
        _row(
            5,
            46,
            status="expired",
            assessment={"findings": ["secret-finding"]},
            binding_state="cancelled",
            settlement="escrow_created",
            lease_pr=3,
            binding_pr=13,
            amount=15,
            payout="not_paid",
            pr_number=3,
        ),
        _row(
            6,
            45,
            status="settled",
            assessment={"outcome": "UNCONFIGURED"},
            binding_state="settlement_pending",
            settlement="needs_review",
            binding_pr=6,
            amount=60,
            payout="pending",
            pr_number=6,
        ),
        _row(
            7,
            44,
            status="settled",
            assessment={"outcome": "POLICY_VIOLATION"},
            binding_state="settlement_pending",
            settlement="delivered",
            lease_pr=2,
            amount=70,
            payout="pending",
            pr_number=2,
        ),
        _row(
            8,
            43,
            status="work",
            assessment=None,
            binding_state="awaiting_merge",
            settlement="released",
            binding_pr=12,
            amount=90,
            pr_number=12,
        ),
        _row(
            9,
            42,
            status="released",
            assessment={"outcome": "NOT_ACCEPTED"},
            binding_state="cancelled",
            amount=5,
            payout="not_paid",
        ),
        _row(
            11,
            41,
            status="settled",
            assessment={"outcome": "REJECTED_TOO_LARGE"},
            binding_state="advertised",
            settlement="escrow_created",
            amount=33,
            payout="pending",
        ),
        _row(
            20,
            40,
            status="work",
            assessment={"outcome": "acceptable"},
            lease_pr=1,
            amount=1,
            evaluation="acceptable",
            pr_number=1,
        ),
        _row(
            10,
            40,
            status="submitted",
            assessment={"outcome": "needs_review"},
            binding_state="needs_review",
            binding_pr=2,
            amount=2,
            evaluation="needs_review",
            pr_number=2,
        ),
        _row(
            900,
            59,
            user_id=OTHER,
            status="settled",
            assessment=acceptable,
            binding_state="paid",
            settlement="released",
            lease_pr=100,
            amount=1000,
            evaluation="acceptable",
            payout="paid",
            pr_number=100,
        ),
    ]


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
    for model in (GitHubBountyBinding, AcceptanceSpec, GitHubLease, GitHubSettlement):
        model.__table__.create(eng)
    return eng


def _insert(db: _Bridge, row: dict) -> dict:
    lease_id = row["lease_id"]
    binding_id = uuid.uuid4()
    bounty_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    current_id = spec_id
    when = row["created_at"]
    if row["current_amount"] is not None:
        current_id = uuid.uuid4()
    db.sync.add(
        GitHubBountyBinding(
            id=binding_id,
            bounty_id=bounty_id,
            repo_full_name=row["repo"],
            issue_number=row["issue"],
            current_spec_id=current_id,
            pr_number=row["binding_pr"],
            state=row["binding_state"],
            last_assessment=row["assessment"],
            created_at=when,
            updated_at=when,
        )
    )
    db.sync.add(_spec(spec_id, binding_id, 1, row["amount"], lease_id.hex, when))
    if row["current_amount"] is not None:
        db.sync.add(
            _spec(current_id, binding_id, 2, row["current_amount"], f"current-{lease_id.hex}", when)
        )
    db.sync.add(
        GitHubLease(
            id=lease_id,
            binding_id=binding_id,
            agent_id="secret-solver",
            user_id=row["user_id"],
            github_user_id=999001,
            spec_id=spec_id,
            token_hash=f"secret-lease-token-{lease_id.hex}",
            status=row["status"],
            work_expires_at=row["work_expires_at"],
            hard_expires_at=row["work_expires_at"],
            hold_escrow_id=f"secret-hold-escrow-{lease_id.hex}",
            hold_cancel_secret=f"secret-hold-cancel-{lease_id.hex}",
            pr_number=row["lease_pr"],
            created_at=when,
            updated_at=when,
        )
    )
    if row["settlement"] is not None:
        db.sync.add(
            GitHubSettlement(
                id=uuid.uuid4(),
                binding_id=binding_id,
                spec_id=spec_id,
                solver_agent_id="secret-solver",
                head_sha=f"secret-head-{lease_id.hex}",
                state=row["settlement"],
                escrow_id=f"secret-settle-escrow-{lease_id.hex}",
                cancel_secret=f"secret-settle-cancel-{lease_id.hex}",
                idempotency_prefix=f"secret-idem-{lease_id.hex}",
                created_at=when,
                updated_at=when,
            )
        )
    public = dict(row["public"])
    public["bounty_id"] = str(bounty_id)
    return public


def _spec(spec_id, binding_id, version: int, amount: int, suffix: str, when: datetime):
    return AcceptanceSpec(
        id=spec_id,
        binding_id=binding_id,
        version=version,
        content_hash=f"secret-content-hash-{suffix}",
        test_command="secret-test-command",
        required_checks=[],
        policy={"note": "secret-finding"},
        payment_trigger="merge_with_checks",
        amount_ate=amount,
        fee_bps=0,
        required_kya=1,
        required_ema=0.5,
        created_at=when,
    )


def _seed(db: _Bridge) -> tuple[list[dict], str]:
    ranked = []
    other_id = ""
    for row in _rows():
        public = _insert(db, row)
        if row["user_id"] != ME:
            other_id = public["lease_id"]
            continue
        ranked.append((row["created_at"], row["lease_id"], public))
    db.sync.commit()
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked], other_id


def _add_filler(db: _Bridge, count: int) -> None:
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    for i in range(count):
        n = 1000 + i
        row = _row(n, 0, status="work", amount=1)
        row["created_at"] = base + timedelta(seconds=i)
        row["work_expires_at"] = base + timedelta(seconds=i)
        row["issue"] = n
        _insert(db, row)
    db.sync.commit()


def _client_for(bridge: _Bridge, user_id: uuid.UUID) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda user_id=user_id: SimpleNamespace(id=user_id)
    return app, TestClient(app)


def _get(world, user_id: uuid.UUID | None = None):
    if user_id is not None:
        world["app"].dependency_overrides[get_current_user] = lambda user_id=user_id: (
            SimpleNamespace(id=user_id)
        )
    world["bridge"].queries = 0
    return world["client"].get(URL)


@pytest.fixture
def world():
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    expected, other_lease_id = _seed(bridge)
    app, client = _client_for(bridge, ME)
    try:
        yield {
            "client": client,
            "bridge": bridge,
            "app": app,
            "expected": expected,
            "other_lease_id": other_lease_id,
        }
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def test_only_the_callers_leases_come_back_newest_first(world):
    response = _get(world)
    assert response.status_code == 200, response.status_code
    body = response.json()
    assert body["items"] == world["expected"]
    assert world["other_lease_id"] not in {item["lease_id"] for item in body["items"]}
    assert [item["lease_id"] for item in body["items"]] == [
        item["lease_id"] for item in world["expected"]
    ]


def test_each_payout_and_evaluation_mapping(world):
    response = _get(world)
    assert response.status_code == 200, response.status_code
    items = response.json()["items"]
    assert {item["payout"] for item in items} == {"paid", "pending", "not_paid", None}
    assert {item["evaluation"] for item in items} == {"acceptable", "needs_review", None}
    by_id = {item["lease_id"]: item for item in items}
    for expected in world["expected"]:
        got = by_id[expected["lease_id"]]
        assert got["payout"] == expected["payout"]
        assert got["evaluation"] == expected["evaluation"]
        assert got["pr_number"] == expected["pr_number"]
        assert got["amount_ate"] == expected["amount_ate"]
        assert got["status"] == expected["status"]
        assert got["repo"] == expected["repo"]
        assert got["issue_number"] == expected["issue_number"]
        assert got["bounty_id"] == expected["bounty_id"]
        assert got["work_expires_at"] == expected["work_expires_at"]


def test_serialized_response_keys_omit_secrets(world):
    response = _get(world)
    assert response.status_code == 200, response.status_code
    body = response.json()
    assert set(body) == {"items"}
    assert body["items"]
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
    for marker in SECRET_MARKERS:
        assert marker not in response.text
    assert str(ME) not in response.text
    assert str(OTHER) not in response.text
    assert "9999" not in response.text


def test_signed_out_is_401(world):
    world["app"].dependency_overrides.pop(get_current_user)
    world["bridge"].queries = 0
    response = world["client"].get(URL)
    assert response.status_code == 401
    assert world["bridge"].queries == 0
    for marker in SECRET_MARKERS:
        assert marker not in response.text


def test_query_count_stays_bounded_as_rows_grow(world):
    empty = _get(world, user_id=uuid.uuid4())
    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    assert world["bridge"].queries == 1

    first = _get(world, user_id=ME)
    assert first.status_code == 200
    assert len(first.json()["items"]) == len(world["expected"])
    assert world["bridge"].queries == 1

    _add_filler(world["bridge"], 200)
    second = _get(world, user_id=ME)
    assert second.status_code == 200
    body = second.json()
    assert len(body["items"]) == 100
    assert world["bridge"].queries == 1
    assert [item["lease_id"] for item in body["items"][: len(world["expected"])]] == [
        item["lease_id"] for item in world["expected"]
    ]
    assert world["other_lease_id"] not in {item["lease_id"] for item in body["items"]}


def test_earlier_lease_on_a_paid_binding_shows_no_payout():
    eng = _engine()
    # SQLite ignores postgresql_where, so the active-lease index would cover every lease.
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_github_leases_active_binding")
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    binding_id, spec_id = uuid.uuid4(), uuid.uuid4()
    when = _created(10)
    bridge.sync.add(
        GitHubBountyBinding(
            id=binding_id,
            bounty_id=uuid.uuid4(),
            repo_full_name="acme/widget",
            issue_number=5,
            current_spec_id=spec_id,
            state="paid",
            created_at=when,
            updated_at=when,
        )
    )
    bridge.sync.add(_spec(spec_id, binding_id, 1, 40, "shared", when))
    for n, user_id, agent, status, second in (
        (1, ME, "solver-a", "expired", 10),
        (2, OTHER, "solver-b", "settled", 20),
    ):
        bridge.sync.add(
            GitHubLease(
                id=_lease_uuid(n),
                binding_id=binding_id,
                agent_id=agent,
                user_id=user_id,
                github_user_id=999000 + n,
                spec_id=spec_id,
                token_hash=f"hash-{n}",
                status=status,
                work_expires_at=_expires(second),
                hard_expires_at=_expires(second),
                created_at=_created(second),
                updated_at=_created(second),
            )
        )
    bridge.sync.add(
        GitHubSettlement(
            id=uuid.uuid4(),
            binding_id=binding_id,
            spec_id=spec_id,
            solver_agent_id="solver-b",
            head_sha="abc",
            state="released",
            idempotency_prefix="gh:shared",
            created_at=when,
            updated_at=when,
        )
    )
    bridge.sync.commit()
    _, client = _client_for(bridge, ME)
    try:
        items = client.get(URL).json()["items"]
        assert [(item["status"], item["payout"]) for item in items] == [("expired", None)]
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()
