"""Verified-fix payout gate and warranty holdback. No database and no network."""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest  # noqa: E402

from app.models.bounty import BountyStatus  # noqa: E402
from app.models.fix_verifier import FixWarranty  # noqa: E402
from app.models.github import GitHubJob  # noqa: E402
from app.services.fix_warranty import split_payout  # noqa: E402
from app.services.github_settlement import advance_settlement  # noqa: E402
from app.services import github_settlement as settlement_mod  # noqa: E402

_SETTLEMENT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
_BINDING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
_SPEC_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
_BOUNTY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a4")
_FUNDER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a5")
_VERIFICATION_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a6")
_SHA = "b" * 40
_OTHER_SHA = "c" * 40
_VULN = "GHSA-aaaa-bbbb-cccc"
_OTHER_VULN = "GHSA-dddd-eeee-ffff"
_FROZEN = datetime(2026, 6, 1, tzinfo=timezone.utc)
_PREFIX = "gh-settle-gate"


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


def _verification(**overrides):
    row = {
        "id": _VERIFICATION_ID,
        "repo_full_name": "octo/hello",
        "fix_sha": _SHA,
        "vuln_id": _VULN,
        "verdict": "verified",
        "receipt_id": "rcpt-1",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def _param(params: dict, name: str):
    for key, value in params.items():
        if name in key:
            return value
    return None


class _World:
    def __init__(self) -> None:
        self.settlement = SimpleNamespace(
            id=_SETTLEMENT_ID,
            binding_id=_BINDING_ID,
            spec_id=_SPEC_ID,
            solver_agent_id="solver-1",
            head_sha=_SHA,
            state="escrow_created",
            escrow_id="esc-1",
            idempotency_prefix=_PREFIX,
            attempts=0,
            last_error=None,
            cancel_secret="cancel-secret",
            created_at=_FROZEN,
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
            id=_SPEC_ID,
            amount_ate=2500,
            fee_bps=1000,
            content_hash="spec-hash",
            payment_trigger="merge_with_checks",
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
            status="settled",
            hold_escrow_id=None,
            hold_cancel_secret=None,
        )
        self.repo_link = SimpleNamespace(installation_id=99, repo_full_name="octo/hello")
        self.ledger = SimpleNamespace(binding_id=_BINDING_ID, status="advertised")
        self.verifications: list = []
        self.warranties: list = []
        self.jobs: list = []
        self.statements: list = []
        self.added: list = []
        self.db = MagicMock()
        self.db.add = self.added.append
        self.partial = MagicMock(return_value={"status": "partially_released"})
        self.release = MagicMock(return_value={"status": "released"})
        self.deliver = MagicMock(return_value={"status": "held"})
        self.create = MagicMock(return_value={"escrow_id": "esc-1"})
        self.get_escrow = MagicMock(return_value={"status": "held", "fee_amount": 1})
        self.refund = MagicMock(side_effect=AssertionError("refund should not be called"))
        self.comment = AsyncMock(return_value=555)

    def bind(self, monkeypatch, **flags) -> None:
        from app.services.github_settlement import SettlementContext

        ctx = SettlementContext(
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

        async def _load(_db, settlement_id):
            assert settlement_id == self.settlement.id
            return ctx

        async def _execute(stmt):
            self.statements.append(stmt)
            entity = stmt.column_descriptions[0]["entity"]
            if entity.__name__ == "FixVerification":
                return _Rows(_matching_verifications(stmt, self.verifications))
            if entity.__name__ == "FixWarranty":
                return _Rows(_matching_warranties(stmt, self.warranties))
            if entity.__name__ == "GitHubJob":
                return _Rows(self.jobs)
            raise AssertionError(f"unexpected query for {entity}")

        async def _commit():
            return None

        self.db.execute = _execute
        self.db.commit = _commit
        settings = {
            "GITHUB_SETTLEMENT_ENABLED": True,
            "ESCROW_TTL_MINUTES": 90,
            "FIX_VERIFIER_PAYOUT_GATE": False,
            "FIX_WARRANTY_ENABLED": False,
            "FIX_WARRANTY_DAYS": 90,
            "FIX_WARRANTY_HOLDBACK_PCT": 20,
        }
        settings.update(flags)
        monkeypatch.setattr(settlement_mod, "load_settlement_graph", _load)
        monkeypatch.setattr(settlement_mod, "settings", SimpleNamespace(**settings))
        monkeypatch.setattr(settlement_mod, "_utcnow", lambda: _FROZEN)
        monkeypatch.setattr(settlement_mod.exchange, "create_locked_escrow", self.create)
        monkeypatch.setattr(settlement_mod.exchange, "deliver", self.deliver)
        monkeypatch.setattr(settlement_mod.exchange, "release_escrow", self.release)
        monkeypatch.setattr(settlement_mod.exchange, "partial_release", self.partial)
        monkeypatch.setattr(settlement_mod.exchange, "refund_escrow", self.refund)
        monkeypatch.setattr(settlement_mod.exchange, "get_escrow", self.get_escrow)
        monkeypatch.setattr(settlement_mod.github_app, "upsert_sticky_comment", self.comment)
        monkeypatch.setattr(
            settlement_mod.github_comments, "render_paid", MagicMock(return_value="paid")
        )
        monkeypatch.setattr(
            settlement_mod.github_comments, "bounty_marker", MagicMock(return_value="marker")
        )

    def run(self):
        return asyncio.run(advance_settlement(self.db, self.settlement.id))


def _compile_params(stmt) -> dict:
    return stmt.compile().params


def _matching_verifications(stmt, rows):
    params = _compile_params(stmt)
    repo = _param(params, "repo_full_name")
    sha = _param(params, "fix_sha")
    vuln = _param(params, "vuln_id")
    matched = []
    for row in rows:
        if repo is not None and row.repo_full_name != repo:
            continue
        if sha is not None and row.fix_sha != sha:
            continue
        if vuln is not None and row.vuln_id != vuln:
            continue
        matched.append(row)
    return matched


def _matching_warranties(stmt, rows):
    params = _compile_params(stmt)
    binding_id = _param(params, "binding_id")
    if binding_id is None:
        return list(rows)
    return [row for row in rows if row.binding_id == binding_id]


def _warranties(world: _World) -> list:
    return [row for row in world.added if isinstance(row, FixWarranty)]


def _jobs(world: _World) -> list:
    return [row for row in world.added if isinstance(row, GitHubJob)]


@pytest.mark.parametrize(
    ("amount", "pct", "holdback", "percent"),
    [
        (2500, 20, 500, 80),
        (100, 20, 20, 80),
        (5, 20, 1, 80),
        (3, 20, 1, 66),
        (1, 20, 1, 0),
        (101, 20, 21, 79),
        (99, 20, 20, 79),
        (6, 20, 2, 66),
        (100, 1, 1, 99),
        (0, 20, 0, 0),
        (50, 0, 0, 100),
        (10, 100, 10, 0),
    ],
)
def test_split_holdback_is_ceiled_and_percent_cannot_overpay(amount, pct, holdback, percent):
    split = split_payout(amount, pct)
    assert split.holdback_cents == holdback
    assert split.release_cents == amount - holdback
    assert split.release_percent == percent
    if amount > 0 and percent not in (0, 100):
        assert (amount * percent) // 100 <= split.release_cents


def test_flags_off_pays_the_full_escrow(monkeypatch):
    world = _World()
    world.settlement.state = "pending"
    world.settlement.escrow_id = None
    world.verifications = [_verification(verdict="incomplete_fix_confirmed", receipt_id=None)]
    world.bind(monkeypatch)

    async def _explode(stmt):
        raise AssertionError(f"gate queried {stmt.column_descriptions[0]['entity']}")

    world.db.execute = _explode
    world.partial.side_effect = AssertionError("partial release should not be called")
    result = world.run()
    assert result.state == "released"
    assert world.deliver.call_count == 1
    assert world.release.call_count == 1
    assert world.release.call_args.kwargs["idempotency_key"] == f"{_PREFIX}:release"
    assert world.binding.state == "paid"
    assert world.ledger.status == "settled"
    assert world.bounty.status == BountyStatus.COMPLETED
    assert _warranties(world) == []
    assert _jobs(world) == []


def test_warranty_flag_without_the_gate_still_pays_in_full(monkeypatch):
    world = _World()
    world.bind(monkeypatch, FIX_WARRANTY_ENABLED=True)
    world.partial.side_effect = AssertionError("partial release should not be called")
    world.run()
    assert world.settlement.state == "released"
    assert world.release.call_count == 1
    assert _warranties(world) == []


def test_verified_receipt_pays_in_full_when_warranty_is_off(monkeypatch):
    world = _World()
    world.verifications = [_verification()]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True)
    world.partial.side_effect = AssertionError("partial release should not be called")
    world.run()
    assert world.settlement.state == "released"
    assert world.release.call_count == 1
    assert world.settlement.last_error is None
    assert _warranties(world) == []


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([], "fix verification missing; payout held"),
        ([_verification(verdict="inconclusive", receipt_id="rcpt-1")], "inconclusive"),
        ([_verification(receipt_id=None)], "receipt missing"),
        ([_verification(receipt_id="  ")], "receipt missing"),
        ([_verification(verdict=None, receipt_id=None)], "not verified"),
        ([_verification(fix_sha=_OTHER_SHA)], "fix verification missing; payout held"),
    ],
)
def test_gate_holds_without_a_verified_receipt(monkeypatch, rows, reason):
    world = _World()
    world.verifications = rows
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True)
    result = world.run()
    assert result is world.settlement
    assert world.settlement.state == "escrow_created"
    assert world.settlement.escrow_id == "esc-1"
    assert reason in (world.settlement.last_error or "")
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.partial.call_count == 0
    assert world.refund.call_count == 0
    (job,) = _jobs(world)
    assert job.kind == "advance_settlement"
    assert job.payload == {"settlement_id": str(_SETTLEMENT_ID)}
    assert job.status == "pending"
    assert job.next_run_at > datetime.now(timezone.utc) + timedelta(minutes=14)
    assert _warranties(world) == []


def test_incomplete_fix_is_not_paid(monkeypatch):
    world = _World()
    world.settlement.state = "pending"
    world.settlement.escrow_id = None
    world.verifications = [_verification(verdict="incomplete_fix_confirmed", receipt_id="rcpt-1")]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True)
    result = world.run()
    assert result.state == "needs_review"
    assert result.escrow_id == "esc-1"
    assert result.last_error == "incomplete fix confirmed; payout refused"
    assert world.create.call_count == 1
    assert world.deliver.call_count == 0
    assert world.release.call_count == 0
    assert world.partial.call_count == 0
    assert world.refund.call_count == 0
    assert _jobs(world) == []
    assert world.comment.await_args.args[3] == "<!-- settlebridge:fix-verification -->"
    body = world.comment.await_args.args[4]
    assert "will not be paid" in body
    assert "cancel-secret" not in body


def test_known_vuln_does_not_accept_a_different_advisory(monkeypatch):
    world = _World()
    world.binding.last_assessment = {"vuln_id": _VULN, "assessment_diff_sha256": "diffsha"}
    world.verifications = [
        _verification(verdict="verified", vuln_id=_OTHER_VULN, receipt_id="rcpt-other"),
        _verification(verdict="incomplete_fix_confirmed", vuln_id=_VULN, receipt_id="rcpt-1"),
    ]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True)
    world.run()
    assert world.settlement.state == "needs_review"
    assert world.deliver.call_count == 0
    assert any(_param(_compile_params(stmt), "vuln_id") == _VULN for stmt in world.statements)


def test_known_vuln_pays_when_that_advisory_is_verified(monkeypatch):
    world = _World()
    world.binding.last_assessment = {"vuln_id": _VULN}
    world.verifications = [
        _verification(verdict="incomplete_fix_confirmed", vuln_id=_OTHER_VULN, receipt_id=None),
        _verification(),
    ]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True, FIX_WARRANTY_ENABLED=False)
    world.partial.side_effect = AssertionError("partial release should not be called")
    world.run()
    assert world.settlement.state == "released"
    assert world.release.call_count == 1


def test_unknown_vuln_incomplete_blocks_even_if_another_row_is_verified(monkeypatch):
    world = _World()
    world.verifications = [
        _verification(),
        _verification(
            id=uuid.uuid4(),
            vuln_id=_OTHER_VULN,
            verdict="incomplete_fix_confirmed",
            receipt_id="rcpt-2",
        ),
    ]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True)
    world.run()
    assert world.settlement.state == "needs_review"
    assert world.deliver.call_count == 0
    assert all(_param(_compile_params(stmt), "vuln_id") is None for stmt in world.statements)


@pytest.mark.parametrize(
    ("amount", "percent", "holdback"),
    [(2500, 80, 500), (101, 79, 21), (1, None, 1)],
)
def test_warranty_releases_the_immediate_share_and_holds_the_ceil(
    monkeypatch, amount, percent, holdback
):
    world = _World()
    world.spec.amount_ate = amount
    world.verifications = [_verification()]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True, FIX_WARRANTY_ENABLED=True)
    world.run()
    assert world.settlement.state == "released"
    assert world.settlement.last_error is None
    assert world.release.call_count == 0
    assert world.refund.call_count == 0
    (warranty,) = _warranties(world)
    assert isinstance(warranty, FixWarranty)
    assert warranty.binding_id == _BINDING_ID
    assert warranty.verification_id == _VERIFICATION_ID
    assert warranty.holdback_cents == holdback
    assert warranty.state == "active"
    assert warranty.window_ends_at == _FROZEN + timedelta(days=90)
    assert world.binding.state == "paid"
    if percent is None:
        assert world.partial.call_count == 0
    else:
        assert world.partial.call_count == 1
        assert world.partial.call_args.args[0] is world.funder
        assert world.partial.call_args.args[1] == "esc-1"
        assert world.partial.call_args.kwargs["release_percent"] == percent
        assert world.partial.call_args.kwargs["efficacy_check_at"] == (
            _FROZEN + timedelta(days=90)
        ).isoformat()
        assert "forfeit" in world.partial.call_args.kwargs["efficacy_criteria"]


def test_retry_after_partial_release_does_not_release_again(monkeypatch):
    world = _World()
    world.settlement.state = "delivered"
    world.get_escrow.return_value = {"status": "partially_released", "fee_amount": 1}
    world.verifications = [_verification()]
    world.warranties = [
        SimpleNamespace(binding_id=_BINDING_ID, holdback_cents=500, state="active")
    ]
    world.bind(monkeypatch, FIX_VERIFIER_PAYOUT_GATE=True, FIX_WARRANTY_ENABLED=True)
    world.run()
    assert world.partial.call_count == 0
    assert world.release.call_count == 0
    assert world.settlement.state == "released"
    assert _warranties(world) == []
