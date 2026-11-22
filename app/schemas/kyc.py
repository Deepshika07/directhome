from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConsentIn(BaseModel):
    purpose: str = Field(default="KYC_IDENTITY", max_length=60)


class ConsentOut(BaseModel):
    purpose: str
    notice_version: str
    accepted_at: datetime


class PanIn(BaseModel):
    pan: str = Field(min_length=10, max_length=10,
                     examples=["ABCDE1234F"])
    name_as_per_pan: str = Field(min_length=2, max_length=120)


class DocInitIn(BaseModel):
    doc_type_id: int
    filename: str = Field(min_length=1, max_length=200)
    mime: str = Field(max_length=100)
    size_bytes: int = Field(gt=0)


class DocInitOut(BaseModel):
    document_id: int
    upload_url: str      # POST raw file bytes here
    max_bytes: int


class DocOut(BaseModel):
    id: int
    doc_type: str | None = None
    status: str
    mime: str
    size_bytes: int
    uploaded_at: datetime | None


class ClaimIn(BaseModel):
    claim_type_id: int
    owner_name_on_doc: str = Field(min_length=2, max_length=120)


class DecisionIn(BaseModel):
    decision: str = Field(examples=["APPROVE"])   # APPROVE | REJECT
    reason_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ActionIn(BaseModel):
    reason_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class QueueItem(BaseModel):
    subject_type: str
    subject_id: int
    user_id: int
    user_name: str
    status: str
    risk_score: int
    claim_type: str | None
    city: str | None
    age_hours: float
    sla_breached: bool
    requires_admin_review: bool
    created_at: datetime


class ClaimTypeOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    required_doc_codes: list[str]
    requires_admin_review: bool


class DocTypeOut(BaseModel):
    id: int
    code: str
    name: str
    applies_to: str
    claim_type_codes: list[str]


class ReasonOut(BaseModel):
    id: int
    code: str
    name: str
    applies_to: str


class KycMetaOut(BaseModel):
    claim_types: list[ClaimTypeOut]
    document_types: list[DocTypeOut]
    rejection_reasons: list[ReasonOut]
    notice_version: str
    grievance_email: str
    verified_badge_copy: str = (
        "Document-verified owner — identity and ownership documents "
        "reviewed. Not a title guarantee."
    )


class CaseOut(BaseModel):
    subject_type: str
    subject_id: int
    status: str
    owner: dict[str, Any]
    subject: dict[str, Any] | None = None
    checks: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    history: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    linked_accounts: list[dict[str, Any]] = []
    requires_admin_review: bool
