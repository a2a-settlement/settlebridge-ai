"""Solver identity for fix-verification receipts.

``classify_solver`` reports ``house``, ``third_party``, or ``unknown``. A house
solver is a platform-operated principal and must not be described as independent.

Canonical principal string
--------------------------
``{kind}:{value}`` with no spaces. ``kind`` is one of:

- ``user`` — SettleBridge user UUID, lowercase ``8-4-4-4-12``
- ``github`` — GitHub user id, decimal, no leading zeros
- ``agent`` — exchange bot / agent id, stripped, case preserved
- ``exchange`` — exchange principal id from KYA lineage, stripped, case preserved
- ``platform`` — platform operator name, lowercase (``platform:verifier``)

``solver_principal_sha256`` is the SHA-256 hex digest of that UTF-8 string.
There is no ``sha256:`` prefix. A missing principal hashes to nothing (``None``).

When several identifiers exist, the hashed principal is the first that parses
among: explicit principal, agent, user, GitHub user, then the first exchange
principal. The others belong in ``lineage``.

House principals
----------------
``config.py`` has no setting that lists platform principals.
``PLATFORM_ADMIN_USER_IDS`` and ``CHALLENGE_OPERATOR_USER_IDS`` are allow-lists,
not the house-solver set. Callers pass ``house_principals``.

Needed setting: ``PLATFORM_HOUSE_PRINCIPALS``, a comma-separated list of
canonical principal strings. Until it exists, pass the set in. Per-user
``account_kind == "house"`` is the stored house marker;
``principals_for_house_account`` turns that row into canonical principals.
Builtin platform operators (``platform:verifier``, ``platform:challenger``) are
always house, as are any other ``platform:`` principals.

Lineage
-------
Each entry is a canonical principal, or a ``(relation, principal)`` pair.
Identity relations (``owner_user``, ``installation_owner``, ``exchange_bot``,
``operator_account``, ``platform_agent``, ``created_by``, ``principal``) make
the solver house only when that principal is a platform principal. Relations
``verifier``, ``challenger``, ``funder``, ``funder_approver``, and ``repo_owner``
are links that block ``third_party`` (the result is ``unknown`` unless the
solver is already house). Only include a principal in the lineage when it
really is in the solver's lineage. A separate verifier who does not share that
lineage is not a link.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

HOUSE = "house"
THIRD_PARTY = "third_party"
UNKNOWN = "unknown"
SOLVER_KINDS = frozenset({HOUSE, THIRD_PARTY, UNKNOWN})

PLATFORM_HOUSE_PRINCIPALS_SETTING = "PLATFORM_HOUSE_PRINCIPALS"

# challenge_orchestrator.PLATFORM_VERIFIER / PLATFORM_CHALLENGER.
BUILTIN_PLATFORM_PRINCIPALS = frozenset({"platform:verifier", "platform:challenger"})

_KINDS = frozenset({"user", "github", "agent", "exchange", "platform"})
_CONFLICT_RELATIONS = frozenset(
    {
        "verifier",
        "challenger",
        "funder",
        "funder_approver",
        "repo_owner",
    }
)

HOUSE_INDEPENDENCE_STATEMENT = (
    "The fix was produced by a SettleBridge-operated solver. "
    "Verifier and challenger are separate operator identities on the same platform; "
    "this receipt does not claim organizational independence."
)
NEUTRAL_INDEPENDENCE_STATEMENT = (
    "The solver principal is recorded on this receipt. "
    "This receipt does not state an independence conclusion."
)


class LineageLink:
    """One related principal. ``relation`` says why it is in the lineage."""

    __slots__ = ("principal", "relation")

    def __init__(self, relation: str, principal: str):
        self.relation = relation
        self.principal = principal

    def __repr__(self) -> str:
        return f"LineageLink({self.relation!r}, {self.principal!r})"


def canonical_principal(principal, value=None) -> str | None:
    """Return the canonical ``kind:value`` string, or ``None`` when it is unknown.

    ``principal`` may be that string, a ``(kind, value)`` pair, a mapping with
    ``kind`` and ``value``, a ``uuid.UUID`` (user), or a positive ``int``
    (GitHub user id). A bare string that is not ``kind:value`` is unknown,
    except a UUID, which is a user.
    """
    if value is not None:
        return _compose(principal, value)
    if principal is None:
        return None
    if isinstance(principal, uuid.UUID):
        return _compose("user", principal)
    if isinstance(principal, bool):
        return None
    if isinstance(principal, int):
        return _compose("github", principal)
    if isinstance(principal, str):
        return _from_string(principal)
    if isinstance(principal, (tuple, list)) and len(principal) == 2:
        return _compose(principal[0], principal[1])
    if isinstance(principal, dict):
        if principal.get("principal") not in (None, ""):
            return canonical_principal(principal.get("principal"))
        kind = principal.get("kind") or principal.get("type")
        raw = principal.get("value")
        if raw is None:
            raw = principal.get("id")
        return _compose(kind, raw)
    kind = getattr(principal, "kind", None)
    raw = getattr(principal, "value", None)
    if kind is not None and raw is not None:
        return _compose(kind, raw)
    explicit = getattr(principal, "principal", None)
    if isinstance(explicit, str):
        return _from_string(explicit)
    return None


def solver_principal_sha256(principal) -> str | None:
    """SHA-256 hex of the canonical principal, or ``None`` when it is unknown."""
    canonical = canonical_principal(principal)
    if canonical is None:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def principals_for_house_account(user) -> frozenset[str]:
    """Canonical principals for a user with ``account_kind == "house"``."""
    if user is None or getattr(user, "account_kind", None) != "house":
        return frozenset()
    found: list[str] = []
    user_id = getattr(user, "id", None)
    if user_id is not None:
        canonical = canonical_principal("user", user_id)
        if canonical:
            found.append(canonical)
    agent = getattr(user, "exchange_bot_id", None)
    if agent:
        canonical = canonical_principal("agent", agent)
        if canonical:
            found.append(canonical)
    return frozenset(found)


def normalize_lineage(lineage) -> tuple[LineageLink, ...]:
    """Drop entries that have no canonical principal."""
    if lineage is None:
        return ()
    links: list[LineageLink] = []
    for item in lineage:
        link = _one_link(item)
        if link is not None:
            links.append(link)
    return tuple(links)


def classify_solver(principal, *, house_principals, lineage) -> tuple[str, str | None]:
    """Return ``(solver_kind, solver_principal_sha256)``.

    House when ``principal`` or any lineage principal is a platform principal
    (in ``house_principals``, or a ``platform:`` operator). Third party only
    when the principal is known and the lineage has no link to the platform,
    the verifier, the challenger, the funder, the funder-approver, or the repo
    owner. Unknown when the principal is missing or a conflict link remains.
    House wins over a conflict link: a platform solver is never third party.
    """
    canonical = canonical_principal(principal)
    if canonical is None:
        return (UNKNOWN, None)
    links = normalize_lineage(lineage)
    house = _house_set(house_principals)
    related = {canonical, *(link.principal for link in links)}
    if related & house or any(_is_platform(item) for item in related):
        return (HOUSE, solver_principal_sha256(canonical))
    if any(link.relation in _CONFLICT_RELATIONS for link in links):
        return (UNKNOWN, solver_principal_sha256(canonical))
    return (THIRD_PARTY, solver_principal_sha256(canonical))


def independence_statement(solver_kind: str) -> str:
    """Receipt text for ``solver_kind``. House never claims independence.

    ``third_party`` and ``unknown`` share one neutral statement. Any other
    kind uses that same neutral statement.
    """
    if solver_kind == HOUSE:
        return HOUSE_INDEPENDENCE_STATEMENT
    return NEUTRAL_INDEPENDENCE_STATEMENT


def primary_principal(
    *,
    principal=None,
    agent_id=None,
    user_id=None,
    github_user_id=None,
    exchange_principals: Iterable | None = None,
) -> str | None:
    """The one principal a receipt hashes. See the module docstring for order."""
    for candidate in (
        principal,
        ("agent", agent_id) if agent_id else None,
        ("user", user_id) if user_id is not None else None,
        ("github", github_user_id) if github_user_id is not None else None,
    ):
        if candidate is None:
            continue
        canonical = canonical_principal(candidate)
        if canonical:
            return canonical
    for item in exchange_principals or ():
        canonical = canonical_principal(("exchange", item)) or canonical_principal(item)
        if canonical:
            return canonical
    return None


def _house_set(house_principals) -> set[str]:
    found = set(BUILTIN_PLATFORM_PRINCIPALS)
    for item in house_principals or ():
        canonical = canonical_principal(item)
        if canonical:
            found.add(canonical)
    return found


def _is_platform(canonical: str) -> bool:
    return canonical.startswith("platform:")


def _one_link(item) -> LineageLink | None:
    if isinstance(item, LineageLink):
        principal = canonical_principal(item.principal)
        relation = str(item.relation or "principal").strip().casefold()
        if principal is None or not relation:
            return None
        return LineageLink(relation, principal)
    if isinstance(item, dict):
        relation = str(item.get("relation") or "principal").strip().casefold()
        principal = canonical_principal(item.get("principal"))
        if principal is None:
            kind = item.get("kind") or item.get("type")
            raw = item.get("value")
            if raw is None:
                raw = item.get("id")
            principal = canonical_principal(kind, raw)
        if principal is None:
            return None
        return LineageLink(relation, principal)
    if isinstance(item, (tuple, list)) and len(item) == 2 and not _is_kind_pair(item):
        relation = str(item[0] or "principal").strip().casefold()
        principal = canonical_principal(item[1])
        if principal is None or not relation:
            return None
        return LineageLink(relation, principal)
    principal = canonical_principal(item)
    if principal is None:
        return None
    return LineageLink("principal", principal)


def _is_kind_pair(item) -> bool:
    return isinstance(item[0], str) and item[0].strip().casefold() in _KINDS


def _from_string(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    kind, sep, raw = stripped.partition(":")
    if sep and kind.strip().casefold() in _KINDS:
        return _compose(kind, raw)
    try:
        parsed = uuid.UUID(stripped)
    except ValueError:
        return None
    return _compose("user", parsed)


def _compose(kind, value) -> str | None:
    if kind is None or value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    normalized = str(kind).strip().casefold()
    if normalized not in _KINDS:
        return None
    if normalized == "user":
        try:
            parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value).strip())
        except (ValueError, AttributeError):
            return None
        return f"user:{parsed}"
    if normalized == "github":
        if isinstance(value, bool) or isinstance(value, float):
            return None
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return f"github:{number}"
    if normalized == "platform":
        name = str(value).strip().casefold()
        if name.startswith("platform:"):
            name = name.partition(":")[2]
        if not name or ":" in name:
            return None
        return f"platform:{name}"
    text = str(value).strip()
    if not text or any(char.isspace() for char in text):
        return None
    return f"{normalized}:{text}"
