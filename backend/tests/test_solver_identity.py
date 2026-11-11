"""Solver kind, principal hash, and independence statements. No network."""

from __future__ import annotations

import hashlib
import uuid

from app.services.solver_identity import (
    HOUSE_INDEPENDENCE_STATEMENT,
    NEUTRAL_INDEPENDENCE_STATEMENT,
    PLATFORM_HOUSE_PRINCIPALS_SETTING,
    canonical_principal,
    classify_solver,
    independence_statement,
    principals_for_house_account,
    solver_principal_sha256,
)

OWNER = uuid.UUID("11111111-1111-4111-8111-111111111111")
FUNDER = uuid.UUID("22222222-2222-4222-8222-222222222222")
OUTSIDER = "agent:outsider"


def _sha(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claims_independence(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    banned = (
        "is independent",
        "are independent",
        "organizationally independent",
        "claims independence",
        "independent of",
    )
    return any(phrase in folded for phrase in banned)


def test_house_when_the_principal_is_platform():
    kind, digest = classify_solver(
        "agent:house-bot",
        house_principals={"agent:house-bot"},
        lineage=(),
    )
    assert kind == "house"
    assert digest == _sha("agent:house-bot")
    assert independence_statement(kind) == HOUSE_INDEPENDENCE_STATEMENT


def test_house_when_the_principal_is_a_platform_operator():
    kind, digest = classify_solver("platform:Verifier", house_principals=(), lineage=())
    assert kind == "house"
    assert canonical_principal("platform:Verifier") == "platform:verifier"
    assert digest == _sha("platform:verifier")


def test_house_when_lineage_owner_is_platform():
    kind, digest = classify_solver(
        "agent:worker-1",
        house_principals={f"USER:{str(OWNER).upper()}"},
        lineage=[
            ("owner_user", str(OWNER)),
            ("installation_owner", OWNER),
            ("exchange_bot", "agent:worker-1"),
            ("created_by", f"user:{FUNDER}"),
        ],
    )
    assert kind == "house"
    assert digest == _sha("agent:worker-1")


def test_house_when_lineage_was_created_by_the_platform():
    kind, _digest = classify_solver(
        "agent:worker-1",
        house_principals=set(),
        lineage=[("created_by", "platform:settlebridge")],
    )
    assert kind == "house"


def test_created_by_a_person_is_not_house():
    kind, _digest = classify_solver(
        OUTSIDER,
        house_principals={"agent:house-bot"},
        lineage=[("created_by", f"user:{FUNDER}"), ("operator_account", f"user:{FUNDER}")],
    )
    assert kind == "third_party"


def test_third_party_when_known_and_unlinked():
    kind, digest = classify_solver(
        OUTSIDER,
        house_principals={"agent:house-bot", "platform:verifier"},
        lineage=[
            ("owner_user", f"user:{FUNDER}"),
            ("exchange_bot", OUTSIDER),
            ("principal", "exchange:solo"),
        ],
    )
    assert kind == "third_party"
    assert digest == _sha(OUTSIDER)
    assert independence_statement(kind) == NEUTRAL_INDEPENDENCE_STATEMENT
    assert independence_statement(kind) != HOUSE_INDEPENDENCE_STATEMENT


def test_unknown_when_principal_missing():
    for principal in (None, "", "   ", "not-a-principal"):
        kind, digest = classify_solver(
            principal,
            house_principals={"agent:house-bot"},
            lineage=[("owner_user", f"user:{OWNER}")],
        )
        assert kind == "unknown"
        assert digest is None


def test_unknown_when_lineage_links_verifier_funder_or_repo_owner():
    relations = ("verifier", "funder", "funder_approver", "repo_owner", "challenger")
    for relation in relations:
        kind, digest = classify_solver(
            OUTSIDER,
            house_principals={"agent:house-bot"},
            lineage=[(relation, f"user:{FUNDER}")],
        )
        assert kind == "unknown"
        assert digest == _sha(OUTSIDER)
        assert independence_statement(kind) == NEUTRAL_INDEPENDENCE_STATEMENT


def test_house_wins_over_a_funder_link():
    kind, digest = classify_solver(
        "agent:house-bot",
        house_principals={"agent:house-bot"},
        lineage=[("funder", f"user:{FUNDER}"), ("repo_owner", "github:7")],
    )
    assert kind == "house"
    assert digest == _sha("agent:house-bot")
    assert not _claims_independence(independence_statement(kind))


def test_sha256_is_deterministic_across_equivalent_forms():
    forms = (
        f"user:{OWNER}",
        ("user", str(OWNER).upper()),
        OWNER,
        {"kind": "user", "value": str(OWNER)},
        {"principal": f"user:{OWNER}"},
    )
    digests = [solver_principal_sha256(item) for item in forms]
    assert len(set(digests)) == 1
    assert digests[0] == _sha(f"user:{OWNER}")
    assert solver_principal_sha256(("github", "0042")) == solver_principal_sha256(42)
    assert solver_principal_sha256(42) == _sha("github:42")
    assert solver_principal_sha256("agent:Bot-1") == _sha("agent:Bot-1")
    assert solver_principal_sha256("agent:Bot-1") != solver_principal_sha256("agent:bot-1")
    first = classify_solver(OUTSIDER, house_principals=set(), lineage=())
    second = classify_solver(OUTSIDER, house_principals=set(), lineage=())
    assert first == second
    assert first[1] != classify_solver("agent:other", house_principals=set(), lineage=())[1]


def test_independence_statement_never_claims_independence():
    assert independence_statement("house") == HOUSE_INDEPENDENCE_STATEMENT
    assert "SettleBridge-operated solver" in HOUSE_INDEPENDENCE_STATEMENT
    assert "does not claim organizational independence" in HOUSE_INDEPENDENCE_STATEMENT
    for kind in ("third_party", "unknown", "anything-else"):
        assert independence_statement(kind) == NEUTRAL_INDEPENDENCE_STATEMENT
    for kind in ("house", "third_party", "unknown"):
        assert not _claims_independence(independence_statement(kind))


def test_house_account_principals_and_missing_setting():
    assert PLATFORM_HOUSE_PRINCIPALS_SETTING == "PLATFORM_HOUSE_PRINCIPALS"
    plain = type("User", (), {"id": OWNER, "exchange_bot_id": "bot-a", "account_kind": None})()
    assert principals_for_house_account(plain) == frozenset()
    house = type("User", (), {"id": OWNER, "exchange_bot_id": "bot-a", "account_kind": "house"})()
    assert principals_for_house_account(house) == frozenset({f"user:{OWNER}", "agent:bot-a"})
    assert principals_for_house_account(None) == frozenset()
