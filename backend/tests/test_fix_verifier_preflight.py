"""Launch preflight for the fix verifier. No network and no database."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import fix_verifier_preflight as preflight

ARN = "arn:aws:kms:us-west-2:123456789012:key/00000000-0000-4000-8000-000000000001"
PEM = "-----BEGIN PRIVATE KEY-----\nLAUNCH-PREFLIGHT-PEM-MARKER\n-----END PRIVATE KEY-----"
SECRET = "launch-preflight-test-secret"

_READY = {
    "FIX_VERIFIER_ENABLED": True,
    "FIX_VERIFIER_PUBLIC_API": True,
    "FIX_VERIFIER_PAYOUT_GATE": True,
    "FIX_WARRANTY_ENABLED": True,
    "CHALLENGE_REPORT_KMS_KEY_ARN": ARN,
    "CHALLENGE_REPORT_KMS_REGION": "us-west-2",
    "CHALLENGE_REPORT_SIGNING_KEY": "",
    "MEDIATOR_URL": "https://mediator.a2a-settlement.org",
    "MEDIATOR_WEBHOOK_SECRET": SECRET,
    "FIX_VERIFIER_DAILY_QUOTA": 20,
    "FIX_VERIFIER_DAILY_COST_CAP_CENTS": 2500,
    "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION": 500,
    "FIX_VERIFIER_RATE_LIMIT_PER_HOUR": 5,
    "FIX_WARRANTY_HOLDBACK_PCT": 20,
    "FIX_WARRANTY_DAYS": 90,
}


def _settings(**overrides: object) -> SimpleNamespace:
    values = dict(_READY)
    values.update(overrides)
    return SimpleNamespace(**values)


def _bind(monkeypatch, **overrides: object) -> None:
    values = dict(_READY)
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(preflight.settings, name, value)


def test_flags_off_skips_launch_invariants():
    report = preflight.check_fix_verifier_launch_config(
        SimpleNamespace(
            FIX_VERIFIER_ENABLED=False,
            FIX_VERIFIER_PUBLIC_API=False,
            FIX_VERIFIER_PAYOUT_GATE=False,
            FIX_WARRANTY_ENABLED=False,
            MEDIATOR_WEBHOOK_SECRET="",
            FIX_WARRANTY_HOLDBACK_PCT=0,
            FIX_WARRANTY_DAYS=1,
        )
    )
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []
    assert report.flags_enabled == {
        "FIX_VERIFIER_ENABLED": False,
        "FIX_VERIFIER_PUBLIC_API": False,
        "FIX_VERIFIER_PAYOUT_GATE": False,
        "FIX_WARRANTY_ENABLED": False,
    }


def test_ready_production_config_passes():
    report = preflight.check_fix_verifier_launch_config(
        _settings(environment="production")
    )
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []
    assert all(report.flags_enabled.values())


def test_alias_arn_passes():
    report = preflight.check_fix_verifier_launch_config(
        _settings(
            CHALLENGE_REPORT_KMS_KEY_ARN=(
                "arn:aws-us-gov:kms:us-west-2:123456789012:alias/challenge-report"
            )
        )
    )
    assert report.ok is True
    assert report.warnings == []


def test_raw_pem_warns_and_does_not_echo_the_key():
    report = preflight.check_fix_verifier_launch_config(
        _settings(CHALLENGE_REPORT_KMS_KEY_ARN="", CHALLENGE_REPORT_SIGNING_KEY=PEM)
    )
    assert report.ok is True
    assert report.errors == []
    assert any("raw PEM" in item for item in report.warnings)
    blob = "\n".join(report.errors + report.warnings)
    assert "LAUNCH-PREFLIGHT-PEM-MARKER" not in blob
    assert SECRET not in blob


def test_missing_signer_is_an_error():
    report = preflight.check_fix_verifier_launch_config(
        _settings(CHALLENGE_REPORT_KMS_KEY_ARN="  ", CHALLENGE_REPORT_SIGNING_KEY="")
    )
    assert report.ok is False
    assert "CHALLENGE_REPORT_KMS_KEY_ARN must be a non-empty KMS key ARN" in report.errors


def test_malformed_arn_is_an_error():
    report = preflight.check_fix_verifier_launch_config(
        _settings(CHALLENGE_REPORT_KMS_KEY_ARN="arn:aws:s3:::not-a-key")
    )
    assert "CHALLENGE_REPORT_KMS_KEY_ARN must be a KMS key ARN" in report.errors


def test_arn_without_region_fails_closed():
    report = preflight.check_fix_verifier_launch_config(
        _settings(CHALLENGE_REPORT_KMS_REGION="")
    )
    assert report.ok is False
    assert any("CHALLENGE_REPORT_KMS_REGION must be set" in item for item in report.errors)


def test_region_mismatch_warns():
    report = preflight.check_fix_verifier_launch_config(
        _settings(CHALLENGE_REPORT_KMS_REGION="us-east-1")
    )
    assert report.ok is True
    assert any("does not match the region" in item for item in report.warnings)


def test_empty_mediator_url_and_secret_fail():
    report = preflight.check_fix_verifier_launch_config(
        _settings(MEDIATOR_URL="", MEDIATOR_WEBHOOK_SECRET="   ")
    )
    assert "MEDIATOR_URL must be a non-empty absolute http(s) URL" in report.errors
    assert "MEDIATOR_WEBHOOK_SECRET must be non-empty" in report.errors
    assert SECRET not in "\n".join(report.errors)


def test_localhost_is_an_error_in_production_and_a_warning_otherwise():
    produced = preflight.check_fix_verifier_launch_config(
        _settings(MEDIATOR_URL="http://127.0.0.1:9/api", environment="production")
    )
    assert any("localhost or loopback" in item for item in produced.errors)
    local = preflight.check_fix_verifier_launch_config(
        _settings(MEDIATOR_URL="https://localhost/mediate")
    )
    assert local.ok is True
    assert any("localhost or loopback" in item for item in local.warnings)
    loopback = preflight.check_fix_verifier_launch_config(
        _settings(MEDIATOR_URL="http://[::1]/", ENVIRONMENT="production")
    )
    assert any("localhost or loopback" in item for item in loopback.errors)


def test_cost_and_quota_bounds():
    zero_quota = preflight.check_fix_verifier_launch_config(
        _settings(FIX_VERIFIER_DAILY_QUOTA=0)
    )
    assert "FIX_VERIFIER_DAILY_QUOTA must be an integer greater than 0" in zero_quota.errors
    over = preflight.check_fix_verifier_launch_config(
        _settings(FIX_VERIFIER_DAILY_COST_CAP_CENTS=2501)
    )
    assert any("1 through 2500" in item and "DAILY_COST_CAP" in item for item in over.errors)
    zero_budget = preflight.check_fix_verifier_launch_config(
        _settings(FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION=0)
    )
    assert any("MAX_COST_CENTS_PER_VERIFICATION" in item for item in zero_budget.errors)
    too_wide = preflight.check_fix_verifier_launch_config(
        _settings(
            FIX_VERIFIER_DAILY_COST_CAP_CENTS=400,
            FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION=500,
        )
    )
    assert any("must not exceed" in item for item in too_wide.errors)
    at_cap = preflight.check_fix_verifier_launch_config(
        _settings(
            FIX_VERIFIER_DAILY_COST_CAP_CENTS=2500,
            FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION=2500,
        )
    )
    assert at_cap.ok is True


def test_bool_is_not_an_integer_quota():
    report = preflight.check_fix_verifier_launch_config(
        _settings(FIX_VERIFIER_DAILY_QUOTA=True)
    )
    assert any("FIX_VERIFIER_DAILY_QUOTA" in item for item in report.errors)


def test_zero_rate_limit_warns():
    report = preflight.check_fix_verifier_launch_config(
        _settings(FIX_VERIFIER_RATE_LIMIT_PER_HOUR=0)
    )
    assert report.ok is True
    assert any("RATE_LIMIT_PER_HOUR" in item for item in report.warnings)


def test_warranty_bounds_and_expected_values():
    off = preflight.check_fix_verifier_launch_config(
        _settings(FIX_WARRANTY_ENABLED=False, FIX_WARRANTY_HOLDBACK_PCT=0, FIX_WARRANTY_DAYS=1)
    )
    assert off.ok is True
    assert off.warnings == []
    low = preflight.check_fix_verifier_launch_config(_settings(FIX_WARRANTY_HOLDBACK_PCT=0))
    assert "FIX_WARRANTY_HOLDBACK_PCT must be an integer from 1 through 50" in low.errors
    high = preflight.check_fix_verifier_launch_config(_settings(FIX_WARRANTY_HOLDBACK_PCT=51))
    assert any("HOLDBACK_PCT" in item for item in high.errors)
    short = preflight.check_fix_verifier_launch_config(_settings(FIX_WARRANTY_DAYS=29))
    assert any("greater than or equal to 30" in item for item in short.errors)
    near = preflight.check_fix_verifier_launch_config(
        _settings(FIX_WARRANTY_HOLDBACK_PCT=10, FIX_WARRANTY_DAYS=30)
    )
    assert near.ok is True
    assert any("HOLDBACK_PCT is 10" in item for item in near.warnings)
    assert any("DAYS is 30" in item for item in near.warnings)


def test_public_api_and_payout_gate_without_the_verifier():
    report = preflight.check_fix_verifier_launch_config(
        SimpleNamespace(
            FIX_VERIFIER_ENABLED=False,
            FIX_VERIFIER_PUBLIC_API=True,
            FIX_VERIFIER_PAYOUT_GATE=True,
            FIX_WARRANTY_ENABLED=False,
            MEDIATOR_WEBHOOK_SECRET="",
        )
    )
    assert report.ok is False
    assert "FIX_VERIFIER_PAYOUT_GATE requires FIX_VERIFIER_ENABLED" in report.errors
    assert any("FIX_VERIFIER_PUBLIC_API is true" in item for item in report.warnings)
    assert not any("MEDIATOR_WEBHOOK_SECRET" in item for item in report.errors)


def test_target_public_launch_simulates_flags_without_mutating():
    raw = SimpleNamespace(
        FIX_VERIFIER_ENABLED=False,
        FIX_VERIFIER_PUBLIC_API=False,
        FIX_VERIFIER_PAYOUT_GATE=False,
        FIX_WARRANTY_ENABLED=False,
        CHALLENGE_REPORT_KMS_KEY_ARN="",
        CHALLENGE_REPORT_SIGNING_KEY="",
        MEDIATOR_URL="http://localhost:8000",
        MEDIATOR_WEBHOOK_SECRET="",
        FIX_VERIFIER_DAILY_QUOTA=20,
        FIX_VERIFIER_DAILY_COST_CAP_CENTS=2500,
        FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION=500,
        FIX_VERIFIER_RATE_LIMIT_PER_HOUR=5,
        FIX_WARRANTY_HOLDBACK_PCT=20,
        FIX_WARRANTY_DAYS=90,
    )
    report = preflight.check_fix_verifier_launch_config(raw, target_public_launch=True)
    assert report.ok is False
    assert all(report.flags_enabled.values())
    assert raw.FIX_VERIFIER_ENABLED is False
    assert any("KMS key ARN" in item for item in report.errors)
    assert any("localhost or loopback" in item for item in report.errors)
    assert any("MEDIATOR_WEBHOOK_SECRET" in item for item in report.errors)


def test_main_json_passes_and_restores_flags(monkeypatch, capsys):
    _bind(monkeypatch, FIX_VERIFIER_ENABLED=True)
    assert preflight.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert payload["warnings"] == []
    assert payload["flags_enabled"]["FIX_VERIFIER_ENABLED"] is True
    assert set(payload) == {"ok", "errors", "warnings", "flags_enabled"}


def test_main_target_public_launch_does_not_flip_settings(monkeypatch, capsys):
    _bind(
        monkeypatch,
        FIX_VERIFIER_ENABLED=False,
        FIX_VERIFIER_PUBLIC_API=False,
        FIX_VERIFIER_PAYOUT_GATE=False,
        FIX_WARRANTY_ENABLED=False,
        CHALLENGE_REPORT_KMS_KEY_ARN="",
        CHALLENGE_REPORT_SIGNING_KEY="",
        MEDIATOR_WEBHOOK_SECRET="",
        MEDIATOR_URL="http://127.0.0.1:9",
    )
    code = preflight.main(["--target-public-launch", "--json"])
    captured = capsys.readouterr()
    assert code == 1
    assert preflight.settings.FIX_VERIFIER_ENABLED is False
    assert preflight.settings.FIX_WARRANTY_ENABLED is False
    payload = json.loads(captured.out)
    assert payload["flags_enabled"]["FIX_VERIFIER_ENABLED"] is True
    assert payload["ok"] is False
    assert captured.err == ""


def test_main_text_reports_a_pem_warning(monkeypatch, capsys):
    _bind(
        monkeypatch,
        CHALLENGE_REPORT_KMS_KEY_ARN="",
        CHALLENGE_REPORT_SIGNING_KEY=PEM,
    )
    assert preflight.main([]) == 0
    text = capsys.readouterr().out
    assert text.startswith("ok: yes\n")
    assert "raw PEM" in text
    assert "LAUNCH-PREFLIGHT-PEM-MARKER" not in text
