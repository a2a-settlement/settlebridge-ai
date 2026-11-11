"""Pure holdback split and follow-up advisory overlap.

Money movement stays in the settlement and warranty watcher, which call the
exchange partial-release, release, and refund functions. The payee's immediate
share is never larger than ``amount - ceil(amount * percent / 100)``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PayoutSplit:
    """Cent split for one verified payout, plus the percent to release now."""

    amount_cents: int
    holdback_cents: int
    release_cents: int
    release_percent: int


def split_payout(amount_cents: int, holdback_pct: int) -> PayoutSplit:
    """Hold back ``ceil(amount * pct / 100)``.

    ``release_percent`` is floored so an integer percent of ``amount_cents``
    cannot exceed ``release_cents``. When the split divides evenly that percent
    is ``100 - pct`` (80 when the holdback is 20).
    """
    amount = _non_negative(amount_cents)
    pct = _non_negative(holdback_pct)
    if pct > 100:
        pct = 100
    if amount == 0 or pct == 0:
        holdback = 0
    else:
        holdback = (amount * pct + 99) // 100
        if holdback > amount:
            holdback = amount
    release = amount - holdback
    if holdback == 0:
        percent = 100 if amount else 0
    elif release <= 0:
        percent = 0
    else:
        percent = (release * 100) // amount
        if percent > 99:
            percent = 99
    return PayoutSplit(
        amount_cents=amount,
        holdback_cents=holdback,
        release_cents=release,
        release_percent=percent,
    )


def verification_locations(verification: object) -> tuple[set[str], set[str]]:
    """Files and functions the verified fix touched.

    Reads ``result`` and ``inputs``. A location may be a file string or a
    mapping with ``file``/``path`` and ``function``/``symbol``.
    """
    files: set[str] = set()
    functions: set[str] = set()
    for attr in ("result", "inputs"):
        part_files, part_functions = locations_of(getattr(verification, attr, None))
        files |= part_files
        functions |= part_functions
    return files, functions


def advisory_overlaps(
    fix_files: set[str],
    fix_functions: set[str],
    advisory: object,
) -> bool:
    """True when a follow-up advisory hits the same fix surface.

    Overlap shares a file. When both sides name functions, they must share a
    function too. A side that names no functions does not narrow the file match.
    """
    adv_files, adv_functions = locations_of(advisory)
    if not (fix_files & adv_files):
        return False
    if fix_functions and adv_functions:
        return bool(fix_functions & adv_functions)
    return True


def advisory_identity(advisory: object) -> str:
    if not isinstance(advisory, dict):
        return ""
    for key in ("id", "vuln_id", "ghsa_id", "cve_id", "cve"):
        value = advisory.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def locations_of(payload: object) -> tuple[set[str], set[str]]:
    """Pull file and function names out of a verification payload or advisory."""
    files: set[str] = set()
    functions: set[str] = set()
    if not isinstance(payload, dict):
        return files, functions
    files |= _names(payload.get("files"))
    files |= _names(payload.get("paths"))
    functions |= _names(payload.get("functions"))
    locations = payload.get("locations")
    if locations is None:
        locations = payload.get("affected")
    if isinstance(locations, list):
        for loc in locations:
            if isinstance(loc, str):
                text = loc.strip()
                if text:
                    files.add(text)
                continue
            if not isinstance(loc, dict):
                continue
            for key in ("file", "path"):
                value = loc.get(key)
                if isinstance(value, str) and value.strip():
                    files.add(value.strip())
                    break
            for key in ("function", "symbol"):
                value = loc.get(key)
                if isinstance(value, str) and value.strip():
                    functions.add(value.strip())
                    break
    return files, functions


def _names(values: object) -> set[str]:
    if isinstance(values, str):
        text = values.strip()
        return {text} if text else set()
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    found: set[str] = set()
    for item in values:
        if isinstance(item, str):
            text = item.strip()
            if text:
                found.add(text)
    return found


def _non_negative(value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    if number < 0:
        return 0
    return number
