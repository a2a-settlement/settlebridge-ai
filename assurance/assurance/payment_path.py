"""The only agent-visible payment tool.

The gateway holds the requester credential and forwards POST /exchange/release.
Actors cannot choose the account, the URL, or instant settlement.
"""

from __future__ import annotations

from dataclasses import dataclass


PAYMENT_TOOL_ID = "request_payment"
RELEASE_PATH = "/exchange/release"


@dataclass
class PaymentDecision:
    status_code: int
    effect: str
    attempted: bool
    realized: bool


class PaymentGateway:
    """In-test stand-in for the shim tool registry plus the exchange policy."""

    def __init__(
        self, *, acceptance_required: bool, approval_bound: bool, released: bool = False
    ):
        self.acceptance_required = acceptance_required
        self.approval_bound = approval_bound
        self.released = released
        self.calls: list[dict] = []

    def request_payment(self, actor_id: str, arguments: dict) -> PaymentDecision:
        if arguments.get("destination_url") or arguments.get("account_id"):
            decision = PaymentDecision(
                403, "actor supplied a destination or account", True, False
            )
        elif self.released:
            decision = PaymentDecision(200, "replay", True, False)
        elif self.acceptance_required and not self.approval_bound:
            decision = PaymentDecision(409, "blocked", True, False)
        else:
            self.released = True
            decision = PaymentDecision(200, "released", True, True)
        self.calls.append(
            {
                "actor_id": actor_id,
                "tool_id": PAYMENT_TOOL_ID,
                "arguments": arguments,
                "decision": decision,
            }
        )
        return decision
