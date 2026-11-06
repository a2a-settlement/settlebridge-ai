"""Per-bounty ATE ledger. Pure functions use fakes; no database."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.middleware.auth import get_current_user
from app.routes.ledger import router
from app.services import exchange, ledger
from app.services.github_bounty_service import escrow_cost

JAN = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)
FEB = datetime(2026, 2, 15, 8, 30, tzinfo=timezone.utc)
MAR = datetime(2026, 3, 15, 8, 30, tzinfo=timezone.utc)
APR = datetime(2026, 4, 1, tzinfo=timezone.utc)
JUNE = datetime(2026, 6, 1, tzinfo=timezone.utc)
FAR = datetime(2030, 1, 1, tzinfo=timezone.utc)
BOUNTY_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
BOUNTY_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
BOUNTY_C = uuid.UUID("00000000-0000-0000-0000-00000000000c")
SECRET = "ate_super_secret_key"
ZERO_BALANCE = {"available": 0, "held_in_escrow": 0, "total_earned": 0, "total_spent": 0}


def test_free_ate_and_unexplained_are_not_clamped():
    assert ledger.free_ate(10, 25) == -15
    assert ledger.free_ate(10, 4) == 6
    assert ledger.unexplained(5, 4, 3) == -2
    assert ledger.unexplained(9, 4, 3) == 2


@pytest.mark.parametrize(
    ("role", "status", "amount", "fee", "holdback", "holdback_fee", "expected"),
    [
        ("requester", "held", 100, 3, 9, 1, 103),
        ("requester", "disputed", 100, 3, 9, 1, 103),
        ("requester", "partially_released", 100, 3, 9, 1, 10),
        ("requester", "released", 100, 3, 9, 1, 0),
        ("requester", "refunded", 100, 3, 0, 0, 0),
        ("provider", "held", 100, 3, 9, 1, 0),
        ("provider", "disputed", 100, 3, 0, 0, 0),
        ("provider", "partially_released", 100, 3, 9, 1, 0),
    ],
)
def test_held_ate(role, status, amount, fee, holdback, holdback_fee, expected):
    escrow = SimpleNamespace(
        status=status,
        amount=amount,
        fee_amount=fee,
        holdback_amount=holdback,
        holdback_fee=holdback_fee,
    )
    assert ledger.held_ate(escrow, role) == expected


def test_held_ate_partial_release_without_holdback_is_zero():
    escrow = SimpleNamespace(status="partially_released", amount=10, fee_amount=1)
    assert ledger.held_ate(escrow, "requester") == 0


def test_committed_ate_uses_advertised_ledger_amounts():
    rows = [
        SimpleNamespace(status="advertised", amount_ate=100),
        SimpleNamespace(status="advertised", amount_ate=40),
        SimpleNamespace(status="released", amount_ate=1000),
        SimpleNamespace(status="settled", amount_ate=1000),
    ]
    assert ledger.committed_ate(rows) == escrow_cost(100) + escrow_cost(40)


@pytest.mark.parametrize(
    ("role", "binding_state", "settlement_state", "leases", "expected"),
    [
        pytest.param(
            "funder", "cancelled", "released", (("work", "h"),), "cancelled", id="cancelled"
        ),
        pytest.param("funder", "advertised", "released", (), "paid", id="paid"),
        pytest.param("funder", "advertised", "pending", (), "settling", id="pending"),
        pytest.param("funder", "advertised", "escrow_created", (), "settling", id="escrow-created"),
        pytest.param("funder", "advertised", "delivered", (), "settling", id="delivered"),
        pytest.param("funder", "advertised", None, (("work", "hold-1"),), "reserved", id="work"),
        pytest.param(
            "funder", "advertised", None, (("submitted", "hold-1"),), "reserved", id="submitted"
        ),
        pytest.param(
            "funder",
            "advertised",
            None,
            (("work", "hold-1"), ("expired", "old")),
            "reserved",
            id="active-beats-expired",
        ),
        pytest.param(
            "funder", "advertised", None, (("expired", "hold-1"),), "refunded", id="expired"
        ),
        pytest.param(
            "funder", "advertised", None, (("released", "hold-1"),), "refunded", id="lease-released"
        ),
        pytest.param(
            "funder",
            "advertised",
            None,
            (("work", None), ("expired", "hold-1")),
            "refunded",
            id="active-without-hold",
        ),
        pytest.param("funder", "advertised", None, (), "advertised", id="open"),
        pytest.param(
            "funder",
            "advertised",
            "failed",
            (("expired", "hold-1"),),
            "advertised",
            id="failed-settlement",
        ),
        pytest.param(
            "funder",
            "advertised",
            "pending",
            (("work", "hold-1"),),
            "settling",
            id="settlement-beats-lease",
        ),
        pytest.param("solver", "advertised", "released", (), "paid", id="solver-paid"),
        pytest.param("solver", "cancelled", "pending", (), "settling", id="solver-settling"),
        pytest.param("solver", "advertised", None, (), "settling", id="solver-no-settlement"),
    ],
)
def test_entry_state(role, binding_state, settlement_state, leases, expected):
    binding = SimpleNamespace(state=binding_state)
    settlement = None
    if settlement_state is not None:
        settlement = SimpleNamespace(state=settlement_state, escrow_id="esc")
    lease_rows = tuple(
        SimpleNamespace(status=status, hold_escrow_id=hold) for status, hold in leases
    )
    assert ledger.entry_state(role, binding, settlement, lease_rows) == expected


def test_entry_escrow_id_prefers_settlement_then_active_hold():
    settlement = SimpleNamespace(escrow_id="settle-1", state="pending")
    leases = (
        SimpleNamespace(status="expired", hold_escrow_id="old"),
        SimpleNamespace(status="work", hold_escrow_id="hold-1"),
    )
    assert ledger.entry_escrow_id(settlement, leases) == "settle-1"
    assert ledger.entry_escrow_id(SimpleNamespace(escrow_id=None), leases) == "hold-1"
    ended = (SimpleNamespace(status="expired", hold_escrow_id="old"),)
    assert ledger.entry_escrow_id(None, ended) is None
    pending = (SimpleNamespace(status="work", hold_escrow_id="pending:5"),)
    assert ledger.entry_escrow_id(None, pending) is None


def test_ledger_csv_rows_and_no_secrets():
    entry = SimpleNamespace(
        kind="github_bounty",
        role="funder",
        bounty_id="b1",
        repo="acme/widgets",
        issue_number=7,
        title='Say "hi", friend',
        pr_number=None,
        state="advertised",
        amount_ate=10,
        fee_ate=1,
        escrow_id=None,
        escrow_status=None,
        updated_at="2026-05-01T12:00:00+00:00",
        issue_url="https://github.com/acme/widgets/issues/7",
        pr_url=None,
        exchange_api_key=SECRET,
    )
    other = SimpleNamespace(
        escrow_id="e1",
        role="requester",
        counterparty_id="bot",
        counterparty_name=None,
        amount=4,
        fee_amount=1,
        status="held",
        held_ate=5,
        efficacy_check_at=None,
        task_id=None,
        created_at="2026-04-01T00:00:00+00:00",
        delivered_content="secret-body",
    )
    text = ledger.ledger_csv(SimpleNamespace(entries=[entry], other_escrows=[other]))
    assert SECRET not in text
    assert "secret-body" not in text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert text.splitlines()[0] == ",".join(ledger.CSV_COLUMNS)
    assert [row["record"] for row in rows] == ["entry", "other_escrow"]
    assert rows[0]["title"] == 'Say "hi", friend'
    assert rows[0]["pr_number"] == ""
    assert rows[0]["amount_ate"] == "10"
    assert rows[1]["escrow_id"] == "e1"
    assert rows[1]["held_ate"] == "5"
    assert rows[1]["counterparty_name"] == ""
    assert rows[1]["title"] == ""


def test_assemble_ledger_money_and_rows():
    funder_a = ledger.FunderRow(
        SimpleNamespace(amount_ate=40, status="advertised", updated_at=JAN),
        SimpleNamespace(
            bounty_id=BOUNTY_A,
            repo_full_name="acme/alpha",
            issue_number=1,
            pr_number=None,
            state="advertised",
        ),
        SimpleNamespace(title="Alpha"),
        SimpleNamespace(amount_ate=50),
        None,
        (SimpleNamespace(status="work", hold_escrow_id="hold-1", updated_at=JUNE),),
    )
    funder_b = ledger.FunderRow(
        SimpleNamespace(amount_ate=10, status="released", updated_at=MAR),
        SimpleNamespace(
            bounty_id=BOUNTY_B,
            repo_full_name="acme/beta",
            issue_number=2,
            pr_number=4,
            state="cancelled",
        ),
        SimpleNamespace(title="Beta"),
        SimpleNamespace(amount_ate=11),
        None,
        (),
    )
    solver_same = ledger.SolverRow(
        SimpleNamespace(status="settled", hold_escrow_id="hold-skip", updated_at=FAR),
        SimpleNamespace(
            bounty_id=BOUNTY_A,
            repo_full_name="acme/alpha",
            issue_number=1,
            pr_number=None,
            state="paid",
        ),
        SimpleNamespace(title="Should skip"),
        SimpleNamespace(amount_ate=50),
        SimpleNamespace(state="released", escrow_id="set-skip", updated_at=FAR),
        (SimpleNamespace(status="settled", hold_escrow_id="hold-skip", updated_at=FAR),),
    )
    solver_c_lease = SimpleNamespace(status="settled", hold_escrow_id="hold-c", updated_at=FEB)
    solver_c = ledger.SolverRow(
        solver_c_lease,
        SimpleNamespace(
            bounty_id=BOUNTY_C,
            repo_full_name="acme/gamma",
            issue_number=3,
            pr_number=12,
            state="paid",
        ),
        SimpleNamespace(title="Gamma"),
        SimpleNamespace(amount_ate=7),
        SimpleNamespace(state="released", escrow_id="set-3", updated_at=JUNE),
        (solver_c_lease,),
    )
    rows = ledger.LedgerRows((funder_a, funder_b), (solver_same, solver_c))
    escrows = [
        {
            "id": "hold-1",
            "requester_id": "me",
            "provider_id": "solver-bot",
            "amount": 50,
            "fee_amount": 2,
            "status": "held",
            "delivered_content": "secret-body",
            "exchange_api_key": SECRET,
        },
        {
            "id": "other-d",
            "requester_id": "me",
            "provider_id": "prov-d",
            "amount": 4,
            "fee_amount": 1,
            "status": "disputed",
            "task_id": "task-9",
            "created_at": "2026-03-02T00:00:00+00:00",
        },
        {
            "id": "other-p",
            "requester_id": "me",
            "provider_id": "prov-p",
            "amount": 100,
            "fee_amount": 3,
            "status": "partially_released",
            "holdback_amount": 7,
            "holdback_fee": 1,
            "counterparty_name": "Pat",
            "efficacy_check_at": JUNE,
            "created_at": APR,
        },
        {
            "id": "prov-hold",
            "requester_id": "someone-else",
            "provider_id": "me",
            "amount": 999,
            "fee_amount": 9,
            "status": "held",
        },
        {
            "id": "done",
            "requester_id": "me",
            "provider_id": "z",
            "amount": 30,
            "fee_amount": 1,
            "status": "released",
        },
        {
            "id": "set-3",
            "requester_id": "other-funder",
            "provider_id": "me",
            "amount": 7,
            "fee_amount": 1,
            "status": "released",
        },
    ]
    result = ledger.assemble_ledger(
        rows,
        account_id="me",
        balance={"available": 100, "held_in_escrow": 70, "total_earned": 3, "total_spent": 4},
        escrows=escrows,
        exchange_error=None,
    )
    committed = escrow_cost(40)
    assert result["account_id"] == "me"
    assert result["exchange_error"] is None
    assert result["committed_ate"] == committed
    assert result["free_ate"] == ledger.free_ate(100, committed)
    assert result["balance"] == {
        "available": 100,
        "held_in_escrow": 70,
        "total_earned": 3,
        "total_spent": 4,
    }
    assert result["held_breakdown"] == {
        "bounty_holds": 52,
        "other_holds": 13,
        "unexplained": 5,
    }
    assert result["other_escrows"] == [
        {
            "escrow_id": "other-d",
            "role": "requester",
            "counterparty_id": "prov-d",
            "counterparty_name": None,
            "amount": 4,
            "fee_amount": 1,
            "status": "disputed",
            "held_ate": 5,
            "efficacy_check_at": None,
            "task_id": "task-9",
            "created_at": "2026-03-02T00:00:00+00:00",
        },
        {
            "escrow_id": "other-p",
            "role": "requester",
            "counterparty_id": "prov-p",
            "counterparty_name": "Pat",
            "amount": 100,
            "fee_amount": 3,
            "status": "partially_released",
            "held_ate": 8,
            "efficacy_check_at": JUNE.isoformat(),
            "task_id": None,
            "created_at": APR.isoformat(),
        },
    ]
    assert [entry["bounty_id"] for entry in result["entries"]] == [
        str(BOUNTY_B),
        str(BOUNTY_C),
        str(BOUNTY_A),
    ]
    assert [entry["role"] for entry in result["entries"]] == ["funder", "solver", "funder"]
    assert [entry["state"] for entry in result["entries"]] == ["cancelled", "paid", "reserved"]
    assert result["entries"][0]["updated_at"] == MAR.isoformat()
    assert result["entries"][1]["updated_at"] == FEB.isoformat()
    assert result["entries"][2]["updated_at"] == JAN.isoformat()
    assert result["entries"][2]["amount_ate"] == 50
    assert result["entries"][2]["fee_ate"] == 2
    assert result["entries"][2]["escrow_id"] == "hold-1"
    assert result["entries"][2]["escrow_status"] == "held"
    assert result["entries"][2]["pr_url"] is None
    assert result["entries"][2]["issue_url"] == "https://github.com/acme/alpha/issues/1"
    assert result["entries"][1]["amount_ate"] == 7
    assert result["entries"][1]["fee_ate"] == 1
    assert result["entries"][1]["escrow_id"] == "set-3"
    assert result["entries"][1]["pr_url"] == "https://github.com/acme/gamma/pull/12"
    assert result["entries"][0]["amount_ate"] == 11
    assert result["entries"][0]["fee_ate"] == 0
    assert result["entries"][0]["escrow_id"] is None
    assert "Should skip" not in [entry["title"] for entry in result["entries"]]
    dumped = json.dumps(result)
    assert SECRET not in dumped
    assert "secret-body" not in dumped
    assert "set-skip" not in dumped


def test_collect_escrows_stops_on_short_page(monkeypatch):
    calls = []

    def fake(_user, *, limit=100, offset=0):
        calls.append((limit, offset))
        if offset == 0:
            return {"escrows": [{"id": f"a{i}"} for i in range(limit)], "total": 150}
        return {"escrows": [{"id": f"b{i}"} for i in range(50)], "total": 150}

    monkeypatch.setattr(exchange, "list_escrows", fake)
    rows = ledger.collect_escrows(SimpleNamespace())
    assert calls == [(100, 0), (100, 100)]
    assert len(rows) == 150


def test_collect_escrows_stops_after_ten_pages(monkeypatch):
    calls = []

    def fake(_user, *, limit=100, offset=0):
        calls.append(offset)
        return {"escrows": [{"id": f"{offset}-{i}"} for i in range(limit)], "total": 5000}

    monkeypatch.setattr(exchange, "list_escrows", fake)
    rows = ledger.collect_escrows(SimpleNamespace())
    assert calls == [page * 100 for page in range(10)]
    assert len(rows) == 1000


def _advertised_rows():
    binding = SimpleNamespace(
        bounty_id=BOUNTY_A,
        repo_full_name="acme/widgets",
        issue_number=7,
        pr_number=9,
        state="advertised",
        exchange_api_key=SECRET,
    )
    entry = SimpleNamespace(
        amount_ate=40,
        status="advertised",
        updated_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )
    funder = ledger.FunderRow(
        entry,
        binding,
        SimpleNamespace(title="Fix the widget"),
        SimpleNamespace(amount_ate=80),
        None,
        (),
    )
    return ledger.LedgerRows((funder,), ())


def test_exchange_failure_keeps_entries_and_class_name(monkeypatch, caplog):
    async def fake_load(_db, _user_id):
        return _advertised_rows()

    def explode(_user):
        raise RuntimeError(f"boom {SECRET}")

    monkeypatch.setattr(ledger, "load_ledger_rows", fake_load)
    monkeypatch.setattr(exchange, "get_balance", explode)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_api_key=SECRET, exchange_bot_id="acct")
    with caplog.at_level(logging.WARNING):
        body = asyncio.run(ledger.get_ledger(None, user))
    assert body["exchange_error"] == "RuntimeError"
    assert body["account_id"] is None
    assert body["balance"] == ZERO_BALANCE
    assert body["entries"][0]["amount_ate"] == 80
    assert body["entries"][0]["role"] == "funder"
    assert body["entries"][0]["state"] == "advertised"
    assert body["entries"][0]["issue_url"] == "https://github.com/acme/widgets/issues/7"
    assert body["entries"][0]["pr_url"] == "https://github.com/acme/widgets/pull/9"
    assert body["committed_ate"] == escrow_cost(40)
    assert body["free_ate"] == -escrow_cost(40)
    dumped = json.dumps(body)
    assert SECRET not in dumped
    assert "boom" not in dumped
    assert SECRET not in caplog.text
    assert "boom" not in caplog.text


def test_list_escrows_failure_zeros_balance(monkeypatch, caplog):
    async def fake_load(_db, _user_id):
        return _advertised_rows()

    def balance(_user):
        return {
            "account_id": "acct-1",
            "available": 500,
            "held_in_escrow": 20,
            "total_earned": 9,
            "total_spent": 8,
        }

    def explode(_user, *, limit=100, offset=0):
        raise ConnectionError(SECRET)

    monkeypatch.setattr(ledger, "load_ledger_rows", fake_load)
    monkeypatch.setattr(exchange, "get_balance", balance)
    monkeypatch.setattr(exchange, "list_escrows", explode)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="acct-1")
    with caplog.at_level(logging.WARNING):
        body = asyncio.run(ledger.get_ledger(None, user))
    assert body["exchange_error"] == "ConnectionError"
    assert body["account_id"] is None
    assert body["balance"] == ZERO_BALANCE
    assert body["entries"]
    assert body["other_escrows"] == []
    assert body["free_ate"] == -escrow_cost(40)
    assert SECRET not in json.dumps(body)
    assert SECRET not in caplog.text


def test_ledger_routes(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    payload = {
        "account_id": "acct",
        "balance": ZERO_BALANCE,
        "committed_ate": 5,
        "free_ate": -5,
        "held_breakdown": {"bounty_holds": 0, "other_holds": 0, "unexplained": 0},
        "entries": [
            {
                "kind": "github_bounty",
                "role": "funder",
                "bounty_id": "b1",
                "repo": "acme/widgets",
                "issue_number": 7,
                "title": "Widget, quoted",
                "pr_number": None,
                "state": "advertised",
                "amount_ate": 10,
                "fee_ate": 1,
                "escrow_id": None,
                "escrow_status": None,
                "updated_at": "2026-05-01T12:00:00+00:00",
                "issue_url": "https://github.com/acme/widgets/issues/7",
                "pr_url": None,
            }
        ],
        "other_escrows": [
            {
                "escrow_id": "e1",
                "role": "requester",
                "counterparty_id": "bot",
                "counterparty_name": None,
                "amount": 4,
                "fee_amount": 1,
                "status": "held",
                "held_ate": 5,
                "efficacy_check_at": None,
                "task_id": None,
                "created_at": "2026-04-01T00:00:00+00:00",
            }
        ],
        "exchange_error": None,
    }

    async def fake_get_ledger(_db, current):
        assert current is user
        return payload

    monkeypatch.setattr(ledger, "get_ledger", fake_get_ledger)

    async def override_db():
        yield None

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    response = client.get("/me/ledger")
    assert response.status_code == 200
    assert response.json() == payload

    exported = client.get("/me/ledger.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert exported.headers["content-disposition"] == "attachment; filename=ledger.csv"
    assert "Widget, quoted" in exported.text
    assert "other_escrow" in exported.text
    assert exported.text.splitlines()[0].startswith("record,")
