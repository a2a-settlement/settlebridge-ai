"""Launch checks before the fix-verifier flags are turned on.

Operators run this on the host before editing ``.env``::

    python -m app.services.fix_verifier_preflight
    python -m app.services.fix_verifier_preflight --target-public-launch
    python -m app.services.fix_verifier_preflight --json --target-public-launch

``--target-public-launch`` checks the loaded settings as if
``FIX_VERIFIER_ENABLED``, ``FIX_VERIFIER_PUBLIC_API``,
``FIX_VERIFIER_PAYOUT_GATE``, and ``FIX_WARRANTY_ENABLED`` were true, and it
applies the production localhost rule. It does not write settings.

``Settings`` has no ``environment`` field. A localhost mediator is refused when
``--target-public-launch`` is set, or when the object carries ``environment``
or ``ENVIRONMENT`` equal to ``production``.

There is no ``fix_verifier_max_cost_cents`` field. The launch bound of 1
through 2500 cents applies to ``FIX_VERIFIER_DAILY_COST_CAP_CENTS`` and to
``FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION``. The per-verification budget
must also fit under the daily cap, because a slot is claimed only when
``spend_cents + budget <= cap``. The mediator secret is
``MEDIATOR_WEBHOOK_SECRET``, unless ``MEDIATOR_VERIFY_PATCH_SECRET`` and
``MEDIATOR_RECEIPTS_SECRET`` are both set, which replace it for those calls.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import settings

_MAX_LAUNCH_COST_CENTS = 2500
_LAUNCH_FLAGS = (
    "FIX_VERIFIER_ENABLED",
    "FIX_VERIFIER_PUBLIC_API",
    "FIX_VERIFIER_PAYOUT_GATE",
    "FIX_WARRANTY_ENABLED",
)
_KMS_ARN = re.compile(
    r"^arn:(aws|aws-cn|aws-us-gov):kms:(?P<region>[a-z0-9-]+):\d{12}:(key|alias)/.+$"
)
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class PreflightReport:
    """Result of one launch check. ``ok`` is true when ``errors`` is empty."""

    ok: bool
    errors: list[str]
    warnings: list[str]
    flags_enabled: dict[str, bool]


def check_fix_verifier_launch_config(
    settings_obj: object,
    *,
    target_public_launch: bool = False,
) -> PreflightReport:
    """Validate launch invariants for the flags that are on.

    ``target_public_launch`` simulates the four launch flags and the production
    localhost rule without modifying ``settings_obj``.
    """
    flags = {
        name: target_public_launch or _flag(settings_obj, name) for name in _LAUNCH_FLAGS
    }
    errors: list[str] = []
    warnings: list[str] = []
    if flags["FIX_VERIFIER_PUBLIC_API"] and not flags["FIX_VERIFIER_ENABLED"]:
        warnings.append(
            "FIX_VERIFIER_PUBLIC_API is true while FIX_VERIFIER_ENABLED is false"
        )
    if flags["FIX_VERIFIER_PAYOUT_GATE"] and not flags["FIX_VERIFIER_ENABLED"]:
        errors.append("FIX_VERIFIER_PAYOUT_GATE requires FIX_VERIFIER_ENABLED")
    production = target_public_launch or _is_production(settings_obj)
    if flags["FIX_VERIFIER_ENABLED"]:
        _check_verifier(settings_obj, errors, warnings, production=production)
    if flags["FIX_WARRANTY_ENABLED"]:
        _check_warranty(settings_obj, errors, warnings)
    return PreflightReport(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        flags_enabled=flags,
    )


def main(argv: list[str] | None = None) -> int:
    """Print a preflight report. Exit 0 when there are no errors."""
    parser = argparse.ArgumentParser(prog="python -m app.services.fix_verifier_preflight")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--target-public-launch",
        action="store_true",
        help="check as if the four launch flags were enabled",
    )
    args = parser.parse_args(argv)
    report = check_fix_verifier_launch_config(
        settings,
        target_public_launch=args.target_public_launch,
    )
    if args.json:
        sys.stdout.write(json.dumps(_report_dict(report), indent=2) + "\n")
    else:
        if args.target_public_launch:
            sys.stdout.write("target_public_launch: yes\n")
        sys.stdout.write(_format_text(report))
    return 0 if report.ok else 1


def _check_verifier(
    settings_obj: object,
    errors: list[str],
    warnings: list[str],
    *,
    production: bool,
) -> None:
    _check_signing(settings_obj, errors, warnings)
    _check_mediator(settings_obj, errors, warnings, production=production)
    _check_cost(settings_obj, errors)
    _check_rate_limit(settings_obj, warnings)


def _check_signing(settings_obj: object, errors: list[str], warnings: list[str]) -> None:
    arn = _text(getattr(settings_obj, "CHALLENGE_REPORT_KMS_KEY_ARN", ""))
    match = _KMS_ARN.fullmatch(arn) if arn else None
    if match is not None:
        region = _text(getattr(settings_obj, "CHALLENGE_REPORT_KMS_REGION", ""))
        if not region:
            errors.append(
                "CHALLENGE_REPORT_KMS_REGION must be set when CHALLENGE_REPORT_KMS_KEY_ARN is set"
            )
        elif region != match.group("region"):
            warnings.append(
                "CHALLENGE_REPORT_KMS_REGION does not match the region in "
                "CHALLENGE_REPORT_KMS_KEY_ARN"
            )
        return
    if arn:
        errors.append("CHALLENGE_REPORT_KMS_KEY_ARN must be a KMS key ARN")
        return
    if _text(getattr(settings_obj, "CHALLENGE_REPORT_SIGNING_KEY", "")):
        warnings.append(
            "CHALLENGE_REPORT_KMS_KEY_ARN is unset; signing would use the raw PEM in "
            "CHALLENGE_REPORT_SIGNING_KEY"
        )
        return
    errors.append("CHALLENGE_REPORT_KMS_KEY_ARN must be a non-empty KMS key ARN")


def _check_mediator(
    settings_obj: object,
    errors: list[str],
    warnings: list[str],
    *,
    production: bool,
) -> None:
    url = _text(getattr(settings_obj, "MEDIATOR_URL", ""))
    host = _http_host(url)
    if host is None:
        errors.append("MEDIATOR_URL must be a non-empty absolute http(s) URL")
    elif _is_local_host(host):
        message = "MEDIATOR_URL must not use a localhost or loopback host for a production launch"
        if production:
            errors.append(message)
        else:
            warnings.append("MEDIATOR_URL uses a localhost or loopback host")
    if _text(getattr(settings_obj, "MEDIATOR_WEBHOOK_SECRET", "")):
        return
    # Without the fallback, each kind of signed call needs its own secret.
    missing = [
        name
        for name in ("MEDIATOR_VERIFY_PATCH_SECRET", "MEDIATOR_RECEIPTS_SECRET")
        if not _text(getattr(settings_obj, name, ""))
    ]
    if len(missing) == 2:
        errors.append("MEDIATOR_WEBHOOK_SECRET must be non-empty")
    elif missing:
        errors.append(f"{missing[0]} or MEDIATOR_WEBHOOK_SECRET must be non-empty")


def _check_cost(settings_obj: object, errors: list[str]) -> None:
    quota = _as_int(getattr(settings_obj, "FIX_VERIFIER_DAILY_QUOTA", None))
    if quota is None or quota <= 0:
        errors.append("FIX_VERIFIER_DAILY_QUOTA must be an integer greater than 0")
    cap = _as_int(getattr(settings_obj, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", None))
    if cap is None or cap <= 0 or cap > _MAX_LAUNCH_COST_CENTS:
        errors.append(
            "FIX_VERIFIER_DAILY_COST_CAP_CENTS must be an integer from 1 through 2500"
        )
    budget = _as_int(getattr(settings_obj, "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION", None))
    if budget is None or budget <= 0 or budget > _MAX_LAUNCH_COST_CENTS:
        errors.append(
            "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION must be an integer "
            "from 1 through 2500"
        )
    if cap is not None and budget is not None and budget > cap:
        errors.append(
            "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION must not exceed "
            "FIX_VERIFIER_DAILY_COST_CAP_CENTS"
        )


def _check_rate_limit(settings_obj: object, warnings: list[str]) -> None:
    limit = _as_int(getattr(settings_obj, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", None))
    if limit is None or limit <= 0:
        warnings.append(
            "FIX_VERIFIER_RATE_LIMIT_PER_HOUR must be a positive integer; "
            "a limit of 0 refuses every verification"
        )


def _check_warranty(settings_obj: object, errors: list[str], warnings: list[str]) -> None:
    percent = _as_int(getattr(settings_obj, "FIX_WARRANTY_HOLDBACK_PCT", None))
    if percent is None or percent <= 0 or percent > 50:
        errors.append("FIX_WARRANTY_HOLDBACK_PCT must be an integer from 1 through 50")
    elif percent != 20:
        warnings.append(f"FIX_WARRANTY_HOLDBACK_PCT is {percent}; the launch value is 20")
    days = _as_int(getattr(settings_obj, "FIX_WARRANTY_DAYS", None))
    if days is None or days < 30:
        errors.append("FIX_WARRANTY_DAYS must be an integer greater than or equal to 30")
    elif days != 90:
        warnings.append(f"FIX_WARRANTY_DAYS is {days}; the launch value is 90")


def _report_dict(report: PreflightReport) -> dict:
    return {
        "ok": report.ok,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "flags_enabled": {name: report.flags_enabled[name] for name in _LAUNCH_FLAGS},
    }


def _format_text(report: PreflightReport) -> str:
    lines = [f"ok: {'yes' if report.ok else 'no'}", "flags:"]
    for name in _LAUNCH_FLAGS:
        lines.append(f"  {name}: {'true' if report.flags_enabled[name] else 'false'}")
    lines.append("errors:")
    lines.extend(f"- {item}" for item in report.errors)
    if not report.errors:
        lines.append("- (none)")
    lines.append("warnings:")
    lines.extend(f"- {item}" for item in report.warnings)
    if not report.warnings:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def _flag(settings_obj: object, name: str) -> bool:
    return _as_bool(getattr(settings_obj, name, False))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in _TRUE_STRINGS
    if isinstance(value, int):
        return value != 0
    return False


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("+"):
            text = text[1:]
        sign = ""
        if text.startswith("-"):
            sign = "-"
            text = text[1:]
        if text.isdigit():
            return int(f"{sign}{text}")
    return None


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _is_production(settings_obj: object) -> bool:
    for name in ("environment", "ENVIRONMENT"):
        raw = getattr(settings_obj, name, None)
        if isinstance(raw, str) and raw.strip().casefold() == "production":
            return True
    return False


def _http_host(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.hostname
    if not host:
        return None
    return host.casefold().rstrip(".")


def _is_local_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_unspecified)


if __name__ == "__main__":
    raise SystemExit(main())
