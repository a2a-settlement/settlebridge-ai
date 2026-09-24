"""Required gateway profiles fail closed. The baseline profile stays permissive."""

import sys
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.gateway.policy_engine import GatewayRequest, PolicyEngine  # noqa: E402


def test_empty_required_profile_blocks():
    engine = PolicyEngine()
    decision = engine.evaluate(GatewayRequest(source_agent="a", target_agent="b"), required=True)
    assert decision.action.value == "block"


def test_missing_escrow_amount_blocks_when_required():
    engine = PolicyEngine()
    engine._policies = engine.load_from_yaml(
        "policies:\n- name: cap\n  match: {all_agents: true}\n  rules:\n  - max_escrow_amount: 100\n"
    )
    decision = engine.evaluate(
        GatewayRequest(
            source_agent="a",
            target_agent="b",
            reputation_score=1.0,
            metadata={"counterparty_allowed": True},
        ),
        required=True,
    )
    assert decision.action.value == "block"
    assert any("escrow_amount" in reason for reason in decision.reasons)


def test_escrow_read_fills_required_inputs():
    engine = PolicyEngine()
    engine._policies = engine.load_from_yaml(
        "policies:\n- name: cap\n  match: {all_agents: true}\n  rules:\n  - max_escrow_amount: 100\n"
    )
    req = GatewayRequest(source_agent="a", target_agent="b")
    engine.apply_escrow_read(
        req,
        {"amount": 10, "reputation_score": 0.8, "counterparty_allowed": True},
    )
    decision = engine.evaluate(req, required=True)
    assert decision.action.value == "approve"


def test_baseline_empty_profile_approves():
    engine = PolicyEngine()
    decision = engine.evaluate(GatewayRequest(source_agent="a", target_agent="b"), required=False)
    assert decision.action.value == "approve"
