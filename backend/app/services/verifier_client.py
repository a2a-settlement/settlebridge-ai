"""Call the private fix verifier, or report that verification could not run.

The public service does not contain verifier logic. ``settlebridge_verifier``
is imported lazily with importlib and the outcome is cached. When the feature
flag is off or that package cannot be imported, every verification is
inconclusive (``infra_error``) and revert classification is ``unknown``, so a
warranty is never forfeited from this process.
"""

from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType

from app.config import settings

logger = logging.getLogger(__name__)

_PACKAGE = "settlebridge_verifier"
_UNSET = object()

_cached_module: ModuleType | None = None
_cached_token: object = _UNSET
_cached_error: str | None = None
_cached = False


def _load_package() -> ModuleType | None:
    """Import the private package once per ``sys.modules`` entry."""
    global _cached_module, _cached_token, _cached_error, _cached
    token = sys.modules.get(_PACKAGE, _UNSET)
    if _cached and token is _cached_token:
        return _cached_module

    module: ModuleType | None = None
    error = "settlebridge_verifier is not installed"
    try:
        imported = importlib.import_module(_PACKAGE)
    except ImportError as exc:
        # The exception text can name a path; keep the log to the type.
        logger.error("settlebridge_verifier import failed (%s)", type(exc).__name__)
    except Exception as exc:
        logger.error("settlebridge_verifier import failed (%s)", type(exc).__name__)
        error = f"settlebridge_verifier import failed: {type(exc).__name__}"
    else:
        if isinstance(imported, ModuleType):
            module = imported
            error = None
        else:
            logger.error("settlebridge_verifier import failed (non-module)")
            error = "settlebridge_verifier import failed: non-module"

    _cached_module = module
    _cached_error = error
    # Re-read so a failed import that left nothing in sys.modules stays a miss,
    # and a later insertion of the package is a different token.
    _cached_token = sys.modules.get(_PACKAGE, _UNSET)
    _cached = True
    return module


def verifier_available() -> bool:
    """True only when the flag is on and the private package imports."""
    if not settings.FIX_VERIFIER_ENABLED:
        return False
    return _load_package() is not None


def _inconclusive(reason: str, detail: str) -> dict:
    return {
        "verdict": "inconclusive",
        "reason": reason,
        "vex_status": "under_investigation",
        "detail": detail,
    }


def _unavailable_detail() -> str:
    if not settings.FIX_VERIFIER_ENABLED:
        return "FIX_VERIFIER_ENABLED is false"
    return _cached_error or "settlebridge_verifier is not installed"


def _ecosystem(request: dict) -> str:
    advisory = request.get("advisory")
    if not isinstance(advisory, dict):
        return ""
    ecosystem = advisory.get("ecosystem")
    if not isinstance(ecosystem, str):
        return ""
    return ecosystem.strip().casefold()


def verify(request: dict) -> dict:
    """Verify one fix, or return an inconclusive result without raising.

    ``request`` mirrors the private VerifyRequest: repo, pr, base_commit,
    fix_commit, advisory (vuln_id, aliases, summary, details, package,
    ecosystem, references), solver_kind (house, third_party, or unknown),
    solver_principal_sha256, budget_cents, and seed.

    A non-PyPI ecosystem is ``unsupported_project`` whether or not the
    verifier is available, so callers learn the scope limit rather than an
    outage. Otherwise an unavailable verifier is ``infra_error``. Exceptions
    from the private call are logged by type only — the message may echo the
    request — and mapped to ``infra_error``.
    """
    if not isinstance(request, dict):
        return _inconclusive("infra_error", "verification request must be a dict")
    if _ecosystem(request) != "pypi":
        return _inconclusive(
            "unsupported_project",
            "launch scope is pure-Python PyPI only",
        )
    if not verifier_available():
        return _inconclusive("infra_error", _unavailable_detail())
    try:
        service = importlib.import_module(f"{_PACKAGE}.service")
        result = service.verify(request)
    except Exception as exc:
        logger.error("fix verifier call failed (%s)", type(exc).__name__)
        return _inconclusive(
            "infra_error",
            f"settlebridge_verifier.service.verify failed: {type(exc).__name__}",
        )
    if isinstance(result, dict):
        return result
    logger.error("fix verifier call failed (non-dict result)")
    return _inconclusive(
        "infra_error",
        "settlebridge_verifier.service.verify failed: non-dict result",
    )


def classify_revert(message: str, linked_texts: list[str]) -> str:
    """Classify a revert with the shared rule, or ``unknown`` if it cannot run.

    The fallback is never ``true``, so a warranty is not forfeited without the
    shared classifier. Any result other than ``true``, ``false`` or ``unknown``
    is also ``unknown``.
    """
    if not verifier_available():
        return "unknown"
    try:
        revert = importlib.import_module(f"{_PACKAGE}.labels.revert")
        result = revert.classify_revert(message, linked_texts)
    except Exception as exc:
        logger.error("revert classification failed (%s)", type(exc).__name__)
        return "unknown"
    if result in ("true", "false", "unknown"):
        return result
    return "unknown"
