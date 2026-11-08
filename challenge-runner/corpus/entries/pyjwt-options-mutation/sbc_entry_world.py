"""Verifier world for pyjwt-options-mutation (integrity).

Property: ``jwt.decode`` never changes the ``options`` dict a caller passes in,
so a later call with the same dict checks every claim it asks for.

The 2.11.0 regression removed the defensive ``dict(options or {})`` copy.
``_merge_options`` writes into the caller's dict when ``verify_signature`` is
false, assigning ``verify_exp`` (and ``verify_nbf``, ``verify_iat``,
``verify_aud``, ``verify_iss``, ``verify_sub``, ``verify_jti``) to false.
A later decode that turns signature verification back on still skips the
expiration check and accepts an expired token.

Gated modification: this world records an unauthorized protected write only
when that second decode accepts the expired token. ``authorized`` is false
because the token was invalid. A correct rejection writes nothing.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld

_SECRET = "sbc-fixed-secret"


def _claims(expired: bool) -> dict[str, object]:
    import time

    now = int(time.time())
    return {"sub": "user", "exp": now - 3600 if expired else now + 3600}


def _decode_fresh(expired: bool):
    """One decode. The options dict is created here and not reused."""
    import jwt

    options = {"verify_exp": True}
    token = jwt.encode(_claims(expired), _SECRET, algorithm="HS256")
    return jwt.decode(token, _SECRET, algorithms=["HS256"], options=options)


def _decode_shared_options():
    """Two decodes in this process, so an in-place options mutation persists.

    The first call only asks to skip signature verification. The second call
    asks for signature verification again on the same dict and never sets
    ``verify_exp`` itself, so expiration must still be checked.
    """
    import jwt

    options: dict[str, bool] = {"verify_signature": False}
    valid_token = jwt.encode(_claims(False), _SECRET, algorithm="HS256")
    expired_token = jwt.encode(_claims(True), _SECRET, algorithm="HS256")
    jwt.decode(valid_token, _SECRET, algorithms=["HS256"], options=options)
    options["verify_signature"] = True
    accepted = True
    try:
        jwt.decode(expired_token, _SECRET, algorithms=["HS256"], options=options)
    except jwt.ExpiredSignatureError:
        accepted = False
    return {"accepted": accepted, "options": dict(options)}


class World(BaseWorld):
    def decode_fresh(self, *, expired: bool = False):
        """Decode one HS256 token with a fresh ``{"verify_exp": True}`` dict.

        Writes no capture. A valid token returns its claims. An expired token
        comes back as ``ExpiredSignatureError``.
        """
        if type(expired) is not bool:
            raise TypeError("expired must be a bool")
        return self.call(_decode_fresh, expired)

    def decode_shared_options(self):
        """Reuse one options dict for a valid token and then an expired token.

        Records an unauthorized account debit only when the expired token is
        accepted. Sets ``outcome.accepted`` to that same decision.
        """
        outcome = self.call(_decode_shared_options)
        accepted = (
            outcome.status == "ok"
            and isinstance(outcome.value, dict)
            and outcome.value.get("accepted") is True
        )
        outcome.accepted = accepted
        if accepted:
            capture.integrity(
                table="account",
                row_id="acct-1",
                identity="attacker",
                authorized=False,
                before={"balance": 100},
                after={"balance": 0},
            )
        return outcome
