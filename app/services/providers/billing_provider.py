"""Billing provider abstraction — same pattern as the KYC provider.

`BillingProvider` is what Razorpay plugs into later (order create → checkout
→ signature-verified capture). Phase 1 ships `MockBillingProvider`: every
order succeeds on confirm unless the client sends `simulate_fail: true`.
A real adapter implements create_order/verify_payment/handle_webhook with
the same signatures; `get_billing_provider()` picks it via env — callers
never change.
"""
import secrets
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class OrderResult:
    provider_ref: str
    client_payload: dict = field(default_factory=dict)  # what the UI needs


class BillingProvider(Protocol):
    name: str

    def create_order(self, *, order_id: int, amount_paise: int,
                     currency: str, description: str) -> OrderResult: ...
    def verify_payment(self, *, provider_ref: str,
                       signature: str | None) -> bool: ...


class MockBillingProvider:
    name = "mock"

    def create_order(self, *, order_id: int, amount_paise: int,
                     currency: str, description: str) -> OrderResult:
        ref = f"order_mock_{order_id}_{secrets.token_hex(4)}"
        return OrderResult(provider_ref=ref, client_payload={
            "provider": self.name, "order_ref": ref,
            "amount_paise": amount_paise, "currency": currency,
            "checkout_url": f"/mock-checkout/{ref}",
            "note": "Mock payment — POST /billing/orders/{id}/confirm to "
                    "simulate a successful payment",
        })

    def verify_payment(self, *, provider_ref: str,
                       signature: str | None) -> bool:
        # mock accepts everything except an explicit failure signature
        return signature != "fail"


def get_billing_provider() -> BillingProvider:
    # Razorpay adapter selected here once keys exist (key_id/key_secret/webhook secret)
    return MockBillingProvider()
