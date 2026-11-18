"""Call the private fix verifier, or report that verification could not run.

The public service does not contain verifier logic. ``settlebridge_verifier``
is imported lazily with importlib and the outcome is cached. When the feature
flag is off or that package cannot be imported, every verification is
inconclusive (``infra_error``) and revert classification is ``unknown``, so a
warranty is never forfeited from this process.

The challenge engine's per-claim verdict rule is reached the same way, through
``precheck_citation`` and ``decide`` below. Those two are not gated by
``FIX_VERIFIER_ENABLED``; their callers sit behind ``CHALLENGE_ENGINE_ENABLED``.
Models are converted at this boundary: ``app.schemas.challenge`` going in, the
private package's own models coming out, re-validated against
``app.schemas.challenge``. When the rule cannot be imported, raises, or returns
something that does not validate, the answer is "not decided" — never a
rejection of the claim and never a verdict the rule did not give.
"""

from __future__ import annotations

import copy
import importlib
import logging
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Literal

from pydantic import BaseModel

from app.config import settings
from app.schemas.challenge import ClaimIn, RunnerResult, ValidationEvidence, ValidationOutcome

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


# ---------------------------------------------------------------- challenge verdict rule

RULE_UNAVAILABLE = "The private verifier is unavailable, so this claim was not decided"
_RULE_FUNCTIONS = ("precheck_citation", "decide")


@dataclass(frozen=True)
class CitationCheck:
    """Answer of the citation precheck.

    ``unavailable`` means the rule did not run. It is not ``invalid``: a caller
    must not reject the claim on it.
    """

    status: Literal["valid", "invalid", "unavailable"]
    detail: str


def _load_validity() -> tuple[ModuleType, ModuleType] | None:
    """The private ``schemas`` and ``validity`` modules, or None."""
    if _load_package() is None:
        return None
    try:
        schemas = importlib.import_module(f"{_PACKAGE}.schemas")
        validity = importlib.import_module(f"{_PACKAGE}.validity")
    except Exception as exc:
        logger.error("verdict rule import failed (%s)", type(exc).__name__)
        return None
    for owner, names in ((schemas, ("ClaimIn", "RunnerResult")), (validity, _RULE_FUNCTIONS)):
        if not all(callable(getattr(owner, name, None)) for name in names):
            logger.error("verdict rule import failed (incomplete module)")
            return None
    return schemas, validity


def validity_available() -> bool:
    """True when the private verdict rule can be imported in this process."""
    return _load_validity() is not None


def _to_private(model_cls, value: BaseModel | None):
    """Rebuild a public model as the private model of the same shape."""
    if value is None:
        return None
    return model_cls.model_validate(value.model_dump(mode="json"))


def _to_public_outcome(result: object) -> ValidationOutcome:
    """Re-validate the private outcome against the public schema, or raise."""
    if isinstance(result, BaseModel):
        data = result.model_dump(mode="json")
    elif isinstance(result, dict):
        data = result
    else:
        raise TypeError("verdict rule returned a non-model result")
    return ValidationOutcome.model_validate(data)


def precheck_citation(claim: ClaimIn, contract_content: dict) -> CitationCheck:
    """Ask the private rule whether the claim's citation holds for the contract."""
    loaded = _load_validity()
    if loaded is None:
        return CitationCheck("unavailable", RULE_UNAVAILABLE)
    schemas, validity = loaded
    try:
        result = validity.precheck_citation(
            _to_private(schemas.ClaimIn, claim), copy.deepcopy(contract_content)
        )
    except Exception as exc:
        logger.error("citation precheck failed (%s)", type(exc).__name__)
        return CitationCheck("unavailable", RULE_UNAVAILABLE)
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], bool)
        and isinstance(result[1], str)
    ):
        return CitationCheck("valid" if result[0] else "invalid", result[1])
    logger.error("citation precheck failed (malformed result)")
    return CitationCheck("unavailable", RULE_UNAVAILABLE)


def _not_decided(tamper_clean: bool, results: list[RunnerResult | None]) -> ValidationOutcome:
    """Inconclusive, with no check recorded as passed by the rule."""
    return ValidationOutcome(
        verdict="inconclusive",
        reason=RULE_UNAVAILABLE,
        evidence=ValidationEvidence(
            citation_valid=False,
            tamper_clean=tamper_clean,
            observer_fired=False,
            execution_ids=[item.execution_id for item in results if item is not None],
            needs_adjudication=False,
        ),
    )


def decide(
    *,
    claim: ClaimIn,
    contract_content: dict,
    tamper_clean: bool,
    target: RunnerResult,
    positive: RunnerResult,
    negative: RunnerResult,
    base: RunnerResult | None,
    reference: RunnerResult | None,
    rederivation: RunnerResult | None,
    reruns: list[RunnerResult],
    minimized: RunnerResult | None,
) -> ValidationOutcome:
    """Decide one claim with the private rule, or return an inconclusive outcome.

    Never raises. The fallback outcome is ``inconclusive`` with
    ``RULE_UNAVAILABLE`` as the reason.
    """
    recorded = [target, positive, negative, base, reference, rederivation, *reruns, minimized]
    loaded = _load_validity()
    if loaded is None:
        return _not_decided(tamper_clean, recorded)
    schemas, validity = loaded
    try:

        def run(value: RunnerResult | None):
            return _to_private(schemas.RunnerResult, value)

        result = validity.decide(
            claim=_to_private(schemas.ClaimIn, claim),
            contract_content=copy.deepcopy(contract_content),
            tamper_clean=tamper_clean,
            target=run(target),
            positive=run(positive),
            negative=run(negative),
            base=run(base),
            reference=run(reference),
            rederivation=run(rederivation),
            reruns=[run(item) for item in reruns],
            minimized=run(minimized),
        )
        return _to_public_outcome(result)
    except Exception as exc:
        logger.error("verdict rule call failed (%s)", type(exc).__name__)
        return _not_decided(tamper_clean, recorded)
