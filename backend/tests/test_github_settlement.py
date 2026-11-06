"""Resumable GitHub bounty settlement. No database and no network."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest  # noqa: E402

from app.models.bounty import Bounty, BountyStatus  # noqa: E402
from app.models.github import (  # noqa: E402
    AcceptanceSpec,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
)
from app.models.user import User  # noqa: E402
from app.services.github_errors import PermanentJobError, RetryableJobError  # noqa: E402
from app.services.github_settlement import (  # noqa: E402
    SettlementContext,
    advance_settlement,
    delivery_content,
    escrow_already_delivered,
    load_settlement_graph,
    next_action,
)
from app.services import github_settlement as settlement_mod  # noqa: E402

_FAILURE = frozenset({"refunded", "expired", "disputed"})
_STATES = ("pending", "escrow_created", "delivered", "released", "failed", "unknown")
_STATUSES = (None, "", "held", "released", "refunded", "expired", "disputed", "partially_released")
_DELIVERED = (None, False, True)

_SETTLEMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_BINDING_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_SPEC_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
_BOUNTY_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")
_FUNDER_ID = uuid.UUID("00000000-0000-0000-0000-000000000005")
_PREFIX = "gh-settle-1"
_RECOVERED = "11111111-2222-3333-4444-555555555555"


def _expected(state: str, escrow_status: str | None, delivered: bool | None) -> str:
    status = escrow_status.lower() if isinstance(escrow_status, str) and escrow_status else None
    if state == "failed":
        return "noop"
    if state == "released":
        return "finalize"
    if state in ("escrow_created", "delivered") and status in _FAILURE:
        return "fail"
    if state == "pending":
        return "create"
    if state == "escrow_created":
        if status == "released" or delivered:
            return "release"
        return "deliver"
    if state == "delivered":
        return "release"
    return "noop"


@pytest.mark.parametrize("state", _STATES)
@pytest.mark.parametrize("escrow_status", _STATUSES)
@pytest.mark.parametrize("delivered", _DELIVERED)
def test_next_action_table(state, escrow_status, delivered):
    assert next_action(state, escrow_status, delivered) == _expected(state, escrow_status, delivered)


def test_next_action_priority_rows():
    assert next_action("pending", None, None) == "create"
    assert next_action("pending", "refunded", True) == "create"
    assert next_action("failed", "held", False) == "noop"
    assert next_action("released", "refunded", False) == "finalize"
    assert next_action("released", "released", True) == "finalize"
    assert next_action("escrow_created", "held", False) == "deliver"
    assert next_action("escrow_created", "held", None) == "deliver"
    assert next_action("escrow_created", "held", True) == "release"
    assert next_action("escrow_created", "released", False) == "release"
    assert next_action("escrow_created", "disputed", True) == "fail"
    assert next_action("escrow_created", "expired", None) == "fail"
    assert next_action("escrow_created", "refunded", False) == "fail"
    assert next_action("delivered", "held", True) == "release"
    assert next_action("delivered", "released", True) == "release"
    assert next_action("delivered", "Refunded", True) == "fail"
    assert next_action("escrow_created", "RELEASED", False) == "release"
    assert next_action("nope", "held", False) == "noop"


def test_escrow_already_delivered_uses_delivery_fields_not_status():
    assert escrow_already_delivered({"status": "held"}) is False
    assert escrow_already_delivered({"status": "held", "delivered_at": None}) is False
    assert escrow_already_delivered({"status": "held", "delivered_at": ""}) is False
    assert escrow_already_delivered({"status": "held", "delivered_at": "2026-11-04T00:00:00Z"})
    assert escrow_already_delivered({"status": "held", "delivered_content": "{}"})
    assert escrow_already_delivered({"status": "held", "delivered_artifact_sha256": "abc"})
    assert escrow_already_delivered({"status": "released"}) is False
    assert escrow_already_delivered(None) is False


def test_delivery_content_shape():
    bare = SimpleNamespace(repo_full_name="octo/hello", pr_number=None, last_assessment=None)
    settlement = SimpleNamespace(head_sha="abc123")
    spec = SimpleNamespace(content_hash="spec-hash")
    assert json.loads(delivery_content(bare, settlement, spec)) == {
        "head_sha": "abc123",
        "repo": "octo/hello",
        "spec_hash": "spec-hash",
    }

    full = SimpleNamespace(
        repo_full_name="octo/hello",
        pr_number=34,
        last_assessment={"assessment_diff_sha256": "primary", "diff_sha256": "other"},
    )
    body = json.loads(delivery_content(full, settlement, spec))
    assert body == {
        "assessment_diff_sha256": "primary",
        "head_sha": "abc123",
        "pr_number": 34,
        "repo": "octo/hello",
        "spec_hash": "spec-hash",
    }

    fallback = SimpleNamespace(
        repo_full_name="octo/hello",
        pr_number=34,
        last_assessment={"diff_sha256": "from-diff"},
    )
    assert json.loads(delivery_content(fallback, settlement, spec))["assessment_diff_sha256"] == (
        "from-diff"
    )


class _World:
    def __init__(self) -> None:
        self.db = MagicMock()
        self.settlement = SimpleNamespace(
            id=_SETTLEMENT_ID,
            binding_id=_BINDING_ID,
            spec_id=_SPEC_ID,
            solver_agent_id="solver-1",
            head_sha="abc123",
            state="pending",
            escrow_id=None,
            idempotency_prefix=_PREFIX,
            attempts=0,
            last_error="stale",
        )
        self.binding = SimpleNamespace(
            id=_BINDING_ID,
            bounty_id=_BOUNTY_ID,
            repo_full_name="octo/hello",
            issue_number=12,
            pr_number=34,
            state="settlement_pending",
            comment_id=None,
            last_assessment={"assessment_diff_sha256": "diffsha"},
        )
        self.spec = SimpleNamespace(
            id=_SPEC_ID, amount_ate=2500, fee_bps=1000, content_hash="spec-hash"
        )
        self.bounty = SimpleNamespace(
            id=_BOUNTY_ID,
            requester_id=_FUNDER_ID,
            status="open",
            escrow_id=None,
            completed_at=None,
        )
        self.funder = SimpleNamespace(
            id=_FUNDER_ID, exchange_bot_id="funder-bot", exchange_api_key="ate_funder"
        )
        self.provider = SimpleNamespace(
            id=uuid.uuid4(), exchange_bot_id="solver-1", exchange_api_key="ate_solver"
        )
        self.lease = SimpleNamespace(
            id=uuid.uuid4(),
            binding_id=_BINDING_ID,
            user_id=self.provider.id,
            agent_id="solver-1",
            spec_id=_SPEC_ID,
            status="settled",
        )
        self.repo_link = SimpleNamespace(installation_id=99, repo_full_name="octo/hello")
        self.ledger = SimpleNamespace(binding_id=_BINDING_ID, status="advertised")
        self.ctx = SettlementContext(
            settlement=self.settlement,
            binding=self.binding,
            spec=self.spec,
            bounty=self.bounty,
            funder=self.funder,
            repo_link=self.repo_link,
            ledger=self.ledger,
            provider=self.provider,
            lease=self.lease,
        )
        self.commits: list[tuple] = []
        self.create = MagicMock(return_value={"escrow_id": "esc-1"})
        self.deliver = MagicMock(return_value={"status": "held"})
        self.release = MagicMock(return_value={"status": "released"})
        self.get_escrow = MagicMock(return_value={"status": "held"})
        self.comment = AsyncMock(return_value=555)
        self.render_paid = MagicMock(return_value="paid-body")
        self.marker = MagicMock(return_value="marker-1")

    def record_commit(self) -> None:
        world = self

        async def _commit() -> None:
            world.commits.append(
                (
                    world.settlement.state,
                    world.binding.state,
                    world.bounty.status,
                    world.ledger.status,
                    world.binding.comment_id,
                )
            )

        self.db.commit = _commit


def _world(monkeypatch, **overrides) -> _World:
    world = _World()
    for name, value in overrides.items():
        setattr(world.settlement, name, value)
    world.record_commit()

    async def _load(_db, settlement_id):
        assert settlement_id == world.settlement.id
        return world.ctx

    monkeypatch.setattr(settlement_mod, "load_settlement_graph", _load)
    monkeypatch.setattr(
        settlement_mod,
        "settings",
        SimpleNamespace(GITHUB_SETTLEMENT_ENABLED=True, ESCROW_TTL_MINUTES=90),
    )
    monkeypatch.setattr(settlement_mod.exchange, "create_escrow", world.create)
    monkeypatch.setattr(settlement_mod.exchange, "deliver", world.deliver)
    monkeypatch.setattr(settlement_mod.exchange, "release_escrow", world.release)
    monkeypatch.setattr(settlement_mod.exchange, "get_escrow", world.get_escrow)
    monkeypatch.setattr(settlement_mod.github_app, "upsert_sticky_comment", world.comment)
    monkeypatch.setattr(settlement_mod.github_comments, "render_paid", world.render_paid)
    monkeypatch.setattr(settlement_mod.github_comments, "bounty_marker", world.marker)
    return world


def _run(world: _World):
    return asyncio.run(advance_settlement(world.db, world.settlement.id))


def _forbid_exchange(world: _World) -> None:
    def _forbid(*_args, **_kwargs):
        raise AssertionError("exchange should not be called")

    world.create.side_effect = _forbid
    world.deliver.side_effect = _forbid
    world.release.side_effect = _forbid
    world.get_escrow.side_effect = _forbid


def test_missing_settled_lease_makes_no_exchange_calls(monkeypatch):
    world = _world(monkeypatch)
    world.ctx.lease = None
    _forbid_exchange(world)
    result = _run(world)
    assert result is world.settlement
    assert world.settlement.state == "pending"
    assert world.settlement.escrow_id is None
    assert world.commits == []
    assert world.create.call_count == 0
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.get_escrow.call_count == 0


def test_disabled_or_missing_solver_stays_pending(monkeypatch):
    for overrides, enabled in (({}, False), ({"solver_agent_id": None}, True)):
        world = _world(monkeypatch, **overrides)
        settlement_mod.settings.GITHUB_SETTLEMENT_ENABLED = enabled
        _forbid_exchange(world)
        result = _run(world)
        assert result is world.settlement
        assert world.settlement.state == "pending"
        assert world.commits == []
        assert world.binding.state == "settlement_pending"


def test_failed_is_terminal(monkeypatch):
    world = _world(monkeypatch, state="failed", escrow_id="esc-1", attempts=3)
    _forbid_exchange(world)
    assert _run(world) is world.settlement
    assert world.settlement.state == "failed"
    assert world.settlement.attempts == 3
    assert world.commits == []


def test_happy_path_commits_each_step_and_finalizes(monkeypatch):
    world = _world(monkeypatch)
    events: list[str] = []

    def _create(*args, **kwargs):
        events.append("create")
        assert args[0] is world.funder
        assert kwargs == {
            "provider_id": "solver-1",
            "amount": 2500,
            "task_id": f"gh:{_BINDING_ID}:{_SPEC_ID}",
            "ttl_minutes": 90,
            "idempotency_key": f"{_PREFIX}:create",
        }
        return {"escrow_id": "esc-1"}

    def _deliver(*args, **kwargs):
        events.append("deliver")
        assert args[0] is world.provider
        assert args[1] == "esc-1"
        assert kwargs == {}
        assert json.loads(args[2]) == {
            "assessment_diff_sha256": "diffsha",
            "head_sha": "abc123",
            "pr_number": 34,
            "repo": "octo/hello",
            "spec_hash": "spec-hash",
        }
        return {"status": "held"}

    def _release(*args, **kwargs):
        events.append("release")
        assert args[0] is world.funder
        assert args[1] == "esc-1"
        assert kwargs == {"idempotency_key": f"{_PREFIX}:release"}
        return {"status": "released"}

    def _get(*args, **kwargs):
        events.append("get")
        assert args[0] is world.funder
        assert args[1] == "esc-1"
        return {"status": "held", "fee_amount": 7}

    world.create.side_effect = _create
    world.deliver.side_effect = _deliver
    world.release.side_effect = _release
    world.get_escrow.side_effect = _get

    result = _run(world)

    assert result is world.settlement
    assert events == ["create", "get", "deliver", "get", "release", "get"]
    assert [row[0] for row in world.commits[:4]] == [
        "escrow_created",
        "delivered",
        "released",
        "released",
    ]
    assert world.commits[2][1] == "settlement_pending"
    assert world.commits[3][1:] == ("paid", BountyStatus.COMPLETED, "settled", None)
    assert world.commits[-1][4] == 555
    assert world.settlement.state == "released"
    assert world.settlement.escrow_id == "esc-1"
    assert world.settlement.attempts == 0
    assert world.settlement.last_error is None
    assert world.binding.state == "paid"
    assert world.binding.comment_id == 555
    assert world.ledger.status == "settled"
    assert world.bounty.status == BountyStatus.COMPLETED
    assert world.bounty.escrow_id == "esc-1"
    assert world.bounty.completed_at is not None
    assert world.bounty.completed_at.tzinfo is not None
    world.render_paid.assert_called_once_with(
        bounty_id=_BOUNTY_ID, escrow_id="esc-1", amount_ate=2500, fee_ate=7
    )
    world.marker.assert_called_once_with(_BOUNTY_ID)
    world.comment.assert_awaited_once_with(
        99, "octo/hello", 12, "marker-1", "paid-body", comment_id=None
    )


def test_paid_comment_omits_fee_when_escrow_lookup_fails(monkeypatch):
    world = _world(monkeypatch)
    world.get_escrow.side_effect = [{"status": "held"}, {"status": "held"}, RuntimeError("down")]
    _run(world)
    assert world.binding.state == "paid"
    world.render_paid.assert_called_once_with(
        bounty_id=_BOUNTY_ID, escrow_id="esc-1", amount_ate=2500, fee_ate=None
    )
    world.comment.assert_awaited_once()


def test_create_accepts_id_field(monkeypatch):
    world = _world(monkeypatch)
    world.create.return_value = {"id": "esc-from-id"}
    _run(world)
    assert world.settlement.escrow_id == "esc-from-id"
    assert world.release.call_args.args[1] == "esc-from-id"


def test_crash_before_create_commit_reuses_idempotency_key(monkeypatch):
    world = _world(monkeypatch)

    async def _boom() -> None:
        raise RuntimeError("db down")

    world.db.commit = _boom
    with pytest.raises(RetryableJobError):
        _run(world)
    assert world.create.call_count == 1
    key = world.create.call_args.kwargs["idempotency_key"]
    assert key == f"{_PREFIX}:create"

    world.settlement.state = "pending"
    world.settlement.escrow_id = None
    world.settlement.attempts = 0
    world.record_commit()
    _run(world)
    assert world.create.call_count == 2
    assert world.create.call_args_list[0].kwargs["idempotency_key"] == key
    assert world.create.call_args_list[1].kwargs["idempotency_key"] == key


def test_persisted_escrow_created_does_not_create_again(monkeypatch):
    world = _world(monkeypatch, state="escrow_created", escrow_id="esc-kept")
    _run(world)
    assert world.create.call_count == 0
    assert world.deliver.call_count == 1
    assert world.deliver.call_args.args[1] == "esc-kept"
    assert world.release.call_count == 1
    assert world.settlement.state == "released"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "held", "delivered_at": "2026-11-04T00:00:00Z"},
        {"status": "held", "delivered_content": '{"repo":"octo/hello"}'},
        {"status": "held", "delivered_artifact_sha256": "abc"},
    ],
)
def test_resume_when_exchange_already_delivered_skips_deliver(monkeypatch, payload):
    world = _world(monkeypatch, state="escrow_created", escrow_id="esc-9")
    world.get_escrow.return_value = payload
    _run(world)
    assert world.create.call_count == 0
    assert world.deliver.call_count == 0
    assert world.release.call_count == 1
    assert world.release.call_args.kwargs["idempotency_key"] == f"{_PREFIX}:release"
    assert [row[0] for row in world.commits[:2]] == ["delivered", "released"]
    assert world.binding.state == "paid"


def test_resume_when_exchange_already_released_skips_release(monkeypatch):
    world = _world(monkeypatch, state="delivered", escrow_id="esc-9")
    world.get_escrow.return_value = {
        "status": "released",
        "delivered_at": "2026-11-04T00:00:00Z",
    }
    _run(world)
    assert world.create.call_count == 0
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    # The second read fetches the fee for the paid comment.
    assert world.get_escrow.call_count == 2
    assert world.settlement.state == "released"
    assert world.binding.state == "paid"
    assert world.ledger.status == "settled"
    assert world.bounty.status == BountyStatus.COMPLETED


def test_escrow_created_and_already_released_skips_both_calls(monkeypatch):
    world = _world(monkeypatch, state="escrow_created", escrow_id="esc-9")
    world.get_escrow.return_value = {"status": "released"}
    _run(world)
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.settlement.state == "released"
    assert world.binding.state == "paid"


def test_duplicate_create_recovers_escrow_id(monkeypatch):
    world = _world(monkeypatch)

    def _create(*_args, **_kwargs):
        err = RuntimeError("Client error '409 Conflict' for url 'https://exchange.example/v1'")
        err.response = SimpleNamespace(  # type: ignore[attr-defined]
            text=(
                "An active escrow already exists for this task_id "
                f"(escrow_id={_RECOVERED})"
            )
        )
        raise err

    world.create.side_effect = _create
    _run(world)
    assert world.create.call_count == 1
    assert world.settlement.escrow_id == _RECOVERED
    assert world.deliver.call_args.args[1] == _RECOVERED
    assert world.release.call_args.args[1] == _RECOVERED
    assert world.settlement.state == "released"


@pytest.mark.parametrize("status", ["refunded", "expired", "disputed"])
def test_terminal_escrow_fails_permanently(monkeypatch, status):
    world = _world(monkeypatch, state="escrow_created", escrow_id="esc-9", attempts=2)
    world.get_escrow.return_value = {"status": status, "delivered_at": "2026-11-04T00:00:00Z"}
    with pytest.raises(PermanentJobError) as caught:
        _run(world)
    assert status in str(caught.value)
    assert world.settlement.state == "failed"
    assert status in world.settlement.last_error
    assert world.settlement.attempts == 2
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.binding.state == "settlement_pending"


def test_missing_exchange_account_is_permanent(monkeypatch):
    world = _world(monkeypatch, attempts=1)

    def _create(*_args, **_kwargs):
        raise ValueError("User has not linked an exchange account")

    world.create.side_effect = _create
    with pytest.raises(PermanentJobError):
        _run(world)
    assert world.settlement.state == "failed"
    assert world.settlement.attempts == 1
    assert "exchange account" in world.settlement.last_error
    assert world.deliver.call_count == 0


def test_solver_without_api_key_fails_before_escrow_is_created(monkeypatch):
    world = _world(monkeypatch)
    world.provider.exchange_api_key = None
    with pytest.raises(PermanentJobError):
        _run(world)
    assert world.settlement.state == "failed"
    assert world.settlement.escrow_id is None
    assert world.create.call_count == 0
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert "exchange account" in world.settlement.last_error


def test_transient_exchange_error_is_retryable(monkeypatch):
    world = _world(monkeypatch, attempts=4)

    def _create(*_args, **_kwargs):
        raise RuntimeError("503 Bearer ate_supersecret from upstream")

    world.create.side_effect = _create
    with pytest.raises(RetryableJobError) as caught:
        _run(world)
    assert world.settlement.attempts == 5
    assert world.settlement.state == "pending"
    assert world.settlement.escrow_id is None
    assert "503" in world.settlement.last_error
    assert "ate_supersecret" not in world.settlement.last_error
    assert "Bearer" not in (world.settlement.last_error or "")
    assert "ate_supersecret" not in str(caught.value)
    assert world.deliver.call_count == 0


def test_comment_failure_does_not_undo_paid(monkeypatch):
    world = _world(monkeypatch)
    events: list[object] = []

    async def _commit() -> None:
        events.append(("commit", world.binding.state, world.settlement.state, world.bounty.status))

    async def _comment(*_args, **_kwargs):
        events.append("comment")
        raise RuntimeError("github 502")

    async def _rollback() -> None:
        world.binding.state = "settlement_pending"
        world.ledger.status = "advertised"
        world.bounty.status = "open"
        world.settlement.state = "pending"

    world.db.commit = _commit
    world.db.rollback = _rollback
    world.comment.side_effect = _comment

    result = _run(world)
    assert result.state == "released"
    comment_at = events.index("comment")
    assert ("commit", "paid", "released", BountyStatus.COMPLETED) in events[:comment_at]
    assert world.binding.state == "paid"
    assert world.ledger.status == "settled"
    assert world.bounty.status == BountyStatus.COMPLETED
    assert world.bounty.escrow_id == "esc-1"
    assert world.bounty.completed_at is not None
    assert world.settlement.last_error is None


def test_rerun_released_makes_no_exchange_calls(monkeypatch):
    completed = datetime(2026, 11, 4, tzinfo=timezone.utc)
    world = _world(monkeypatch, state="released", escrow_id="esc-1", last_error=None)
    world.binding.state = "paid"
    world.ledger.status = "settled"
    world.bounty.status = BountyStatus.COMPLETED
    world.bounty.completed_at = completed
    world.bounty.escrow_id = "esc-1"
    _forbid_exchange(world)
    _run(world)
    assert world.settlement.state == "released"
    assert world.bounty.completed_at is completed
    assert world.binding.state == "paid"
    assert world.ledger.status == "settled"
    world.comment.assert_awaited()


def _load_graph(lease):
    settlement = SimpleNamespace(
        id=_SETTLEMENT_ID,
        binding_id=_BINDING_ID,
        spec_id=_SPEC_ID,
        solver_agent_id="solver-1",
    )
    binding = SimpleNamespace(
        id=_BINDING_ID, bounty_id=_BOUNTY_ID, repo_full_name="octo/hello"
    )
    spec = SimpleNamespace(id=_SPEC_ID)
    bounty = SimpleNamespace(id=_BOUNTY_ID, requester_id=_FUNDER_ID)
    funder = SimpleNamespace(id=_FUNDER_ID, exchange_api_key="ate_funder")
    provider_id = uuid.uuid4()
    provider = SimpleNamespace(id=provider_id, exchange_api_key="ate_solver")
    if lease is not None:
        lease.user_id = provider_id
    repo_link = SimpleNamespace(installation_id=99)
    ledger = SimpleNamespace(status="advertised")
    rows = {
        GitHubSettlement: settlement,
        GitHubBountyBinding: binding,
        AcceptanceSpec: spec,
        Bounty: bounty,
        GitHubRepoLink: repo_link,
        FunderLedgerEntry: ledger,
        GitHubLease: lease,
    }
    executed = []
    user_calls = {"n": 0}

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    async def _execute(stmt):
        executed.append(stmt)
        entity = stmt.column_descriptions[0]["entity"]
        if entity is User:
            user_calls["n"] += 1
            return _Result(funder if user_calls["n"] == 1 else provider)
        return _Result(rows[entity])

    db = MagicMock()
    db.execute = _execute
    ctx = asyncio.run(load_settlement_graph(db, _SETTLEMENT_ID))
    return ctx, executed, user_calls, provider


def test_loader_locks_settlement_row_and_loads_provider_by_lease_user(monkeypatch):
    lease = SimpleNamespace(binding_id=_BINDING_ID, status="settled", user_id=None)
    ctx, executed, user_calls, provider = _load_graph(lease)
    assert ctx.settlement.binding_id == _BINDING_ID
    assert ctx.binding.id == _BINDING_ID
    assert ctx.spec.id == _SPEC_ID
    assert ctx.bounty.id == _BOUNTY_ID
    assert ctx.funder.id == _FUNDER_ID
    assert ctx.provider is provider
    assert ctx.lease is lease
    assert ctx.repo_link.installation_id == 99
    assert ctx.ledger.status == "advertised"
    assert len(executed) == 9
    assert executed[0]._for_update_arg is not None
    assert all(stmt._for_update_arg is None for stmt in executed[1:])
    assert user_calls["n"] == 2
    user_stmts = [stmt for stmt in executed if stmt.column_descriptions[0]["entity"] is User]
    assert user_stmts[1].whereclause.left.name == "id"
    assert "exchange_bot_id" not in str(user_stmts[0].whereclause)
    assert "exchange_bot_id" not in str(user_stmts[1].whereclause)
    lease_stmt = next(
        stmt for stmt in executed if stmt.column_descriptions[0]["entity"] is GitHubLease
    )
    assert "exchange_bot_id" not in str(lease_stmt)


def test_loader_without_settled_lease_has_no_provider(monkeypatch):
    ctx, executed, user_calls, _provider = _load_graph(None)
    assert ctx.lease is None
    assert ctx.provider is None
    assert user_calls["n"] == 1
    assert len(executed) == 8
    user_stmts = [stmt for stmt in executed if stmt.column_descriptions[0]["entity"] is User]
    assert len(user_stmts) == 1


def _alert_world(monkeypatch, alert_state, *, age_hours=1.0, kind="dependabot"):
    world = _world(monkeypatch)
    world.spec.payment_trigger = settlement_mod.github_alerts.ALERT_FIXED_TRIGGER
    world.settlement.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    settlement_mod.settings.GITHUB_ALERT_FIXED_TIMEOUT_HOURS = 24
    settlement_mod.settings.GITHUB_HOLD_TTL_MARGIN_MINUTES = 360
    world.dispatch = SimpleNamespace(alert_kind=kind, alert_number=7, alert_state="open")
    world.dispatch_lookup = AsyncMock(return_value=world.dispatch)
    world.alert = AsyncMock(return_value={"state": alert_state})
    world.refund = MagicMock(return_value={"status": "refunded"})
    world.added = []
    world.db.add = world.added.append
    monkeypatch.setattr(
        settlement_mod.github_alerts, "dispatch_for_issue", world.dispatch_lookup
    )
    name = "get_dependabot_alert" if kind == "dependabot" else "get_code_scanning_alert"
    monkeypatch.setattr(settlement_mod.github_app, name, world.alert)
    monkeypatch.setattr(settlement_mod.exchange, "refund_escrow", world.refund)
    return world


def test_alert_fixed_pays_and_records_state(monkeypatch):
    world = _alert_world(monkeypatch, "fixed")
    _run(world)
    assert world.settlement.state == "released"
    assert world.dispatch.alert_state == "fixed"
    world.alert.assert_awaited_once_with(99, "octo/hello", 7)
    assert world.create.call_args.kwargs["ttl_minutes"] == 24 * 60 + 360
    assert world.refund.call_count == 0


def test_alert_open_locks_escrow_and_schedules_recheck(monkeypatch):
    world = _alert_world(monkeypatch, "open", kind="code_scanning")
    _run(world)
    assert world.settlement.state == "escrow_created"
    assert world.create.call_count == 1
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    (job,) = world.added
    assert job.kind == "advance_settlement"
    assert job.payload == {"settlement_id": str(_SETTLEMENT_ID)}
    assert job.next_run_at > datetime.now(timezone.utc) + timedelta(minutes=14)


def test_alert_lookup_error_waits(monkeypatch):
    world = _alert_world(monkeypatch, "fixed")
    world.alert.side_effect = RuntimeError("boom")
    _run(world)
    assert world.settlement.state == "escrow_created"
    assert world.deliver.call_count == 0
    assert len(world.added) == 1


def test_alert_not_fixed_after_timeout_refunds_and_fails(monkeypatch):
    world = _alert_world(monkeypatch, "dismissed", age_hours=25)
    with pytest.raises(PermanentJobError):
        _run(world)
    assert world.settlement.state == "failed"
    assert world.refund.call_args.args[:2] == (world.funder, "esc-1")
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.comment.await_args.args[3] == "<!-- settlebridge:alert-fixed -->"
    assert world.added == []


def test_alert_timeout_refund_error_is_retryable(monkeypatch):
    world = _alert_world(monkeypatch, "open", age_hours=25)
    world.refund.side_effect = RuntimeError("exchange down")
    with pytest.raises(RetryableJobError):
        _run(world)
    assert world.settlement.state == "escrow_created"


def test_merge_with_checks_trigger_skips_alert_gate(monkeypatch):
    world = _alert_world(monkeypatch, "open")
    world.spec.payment_trigger = "merge_with_checks"
    _run(world)
    assert world.settlement.state == "released"
    assert world.dispatch_lookup.await_count == 0
    assert world.create.call_args.kwargs["ttl_minutes"] == 90


def test_unadoptable_held_hold_is_refunded_before_create(monkeypatch):
    world = _world(monkeypatch)
    world.lease.hold_escrow_id = "hold-1"
    world.get_escrow.return_value = {"status": "held", "provider_id": "someone-else"}
    refund = MagicMock(return_value={"status": "refunded"})
    monkeypatch.setattr(settlement_mod.exchange, "refund_escrow", refund)
    _run(world)
    assert refund.call_args.args[:2] == (world.funder, "hold-1")
    assert world.settlement.escrow_id == "esc-1"
