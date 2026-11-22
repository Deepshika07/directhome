"""Verification provider abstraction — no vendor lock-in.

`KycProvider` is the interface every vendor (Digio, HyperVerge, IDfy,
Cashfree, Signzy …) plugs into. Phase 1 ships `MockProvider`; a real vendor
implements the same methods and is selected via `get_provider()` — nothing
else in the codebase changes.
"""
import re
from dataclasses import dataclass, field
from typing import Protocol

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


@dataclass
class ProviderResult:
    ok: bool
    result: str                      # PASS / FAIL / REVIEW / NOT_SUPPORTED
    provider_ref: str | None = None
    canonical_name: str | None = None
    score: int | None = None
    detail: dict = field(default_factory=dict)


class KycProvider(Protocol):
    name: str

    def verify_pan(self, pan: str, name: str) -> ProviderResult: ...
    def digilocker_initiate(self, user_id: int, redirect_uri: str) -> ProviderResult: ...
    def digilocker_callback(self, state: str, code: str) -> ProviderResult: ...
    def face_match(self, selfie_key: str, doc_key: str) -> ProviderResult: ...
    def verify_gst(self, gstin: str) -> ProviderResult: ...


class MockProvider:
    """Deterministic mock for Phase 1 manual-review flow.

    Test hooks: a PAN beginning "FAILP" fails verification; a name containing
    "mismatch" forces a REVIEW result. Everything else passes.
    """

    name = "mock"

    def verify_pan(self, pan: str, name: str) -> ProviderResult:
        if not PAN_RE.match(pan):
            return ProviderResult(False, "FAIL", detail={"error": "format"})
        ref = f"mock-pan-{abs(hash((pan, name))) % 10**10}"
        if pan.startswith("FAILP"):
            return ProviderResult(False, "FAIL", provider_ref=ref,
                                  detail={"error": "pan_not_found"})
        if "mismatch" in name.lower():
            return ProviderResult(True, "REVIEW", provider_ref=ref,
                                  canonical_name=name, score=40,
                                  detail={"name_match": "partial"})
        return ProviderResult(True, "PASS", provider_ref=ref,
                              canonical_name=name, score=100,
                              detail={"name_match": "exact"})

    def digilocker_initiate(self, user_id: int, redirect_uri: str) -> ProviderResult:
        return ProviderResult(False, "NOT_SUPPORTED",
                              detail={"error": "digilocker_not_configured"})

    def digilocker_callback(self, state: str, code: str) -> ProviderResult:
        return ProviderResult(False, "NOT_SUPPORTED",
                              detail={"error": "digilocker_not_configured"})

    def face_match(self, selfie_key: str, doc_key: str) -> ProviderResult:
        return ProviderResult(False, "NOT_SUPPORTED",
                              detail={"error": "phase_2_stub"})

    def verify_gst(self, gstin: str) -> ProviderResult:
        return ProviderResult(False, "NOT_SUPPORTED",
                              detail={"error": "phase_2_stub"})


def get_provider() -> KycProvider:
    # Phase 1: always Mock. Real vendor selected here via settings later.
    return MockProvider()
