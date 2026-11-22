from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    DocumentAppliesTo,
    DocumentStatus,
    KycProfileStatus,
    KycSubjectType,
    OwnershipClaimStatus,
    ReviewDecision,
)


# ---------------- lookup tables ----------------

class SellerClaimType(Base):
    """Who the lister claims to be relative to the property."""

    __tablename__ = "seller_claim_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    required_doc_codes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    requires_admin_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KycDocumentType(Base):
    __tablename__ = "kyc_document_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    applies_to: Mapped[DocumentAppliesTo] = mapped_column(
        Enum(DocumentAppliesTo, name="document_applies_to", create_type=False),
        nullable=False,
        server_default=DocumentAppliesTo.BOTH.value,
    )
    claim_type_codes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KycRejectionReason(Base):
    __tablename__ = "kyc_rejection_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    applies_to: Mapped[KycSubjectType] = mapped_column(
        Enum(KycSubjectType, name="kyc_subject_type", create_type=False),
        nullable=False,
        server_default=KycSubjectType.KYC_PROFILE.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------- primary tables ----------------

class KycProfile(Base, TimestampMixin):
    """One identity-KYC profile per user. PAN encrypted at rest; pan_hash
    (HMAC, peppered) is the dedup key — never store the raw number."""

    __tablename__ = "kyc_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    status: Mapped[KycProfileStatus] = mapped_column(
        Enum(KycProfileStatus, name="kyc_profile_status"),
        nullable=False,
        server_default=KycProfileStatus.NOT_STARTED.value,
    )
    level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    name_as_per_pan: Mapped[str | None] = mapped_column(String(120))
    pan_encrypted: Mapped[str | None] = mapped_column(Text)
    pan_hash: Mapped[str | None] = mapped_column(String(64))  # indexed, NOT unique — duplicates are a fraud signal, not a constraint
    aadhaar_last4: Mapped[str | None] = mapped_column(String(4))
    aadhaar_ref: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    user: Mapped["User"] = relationship(
        back_populates="kyc_profile", foreign_keys=[user_id]
    )

    __table_args__ = (
        Index("ix_kyc_profiles_status", "status"),
        Index("ix_kyc_profiles_pan_hash", "pan_hash"),
    )


class KycCheck(Base):
    """Automated/provider check result (PAN verify, digilocker, dup checks)."""

    __tablename__ = "kyc_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="mock"
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)  # PASS/FAIL/REVIEW
    score: Mapped[int | None] = mapped_column(SmallInteger)
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_kyc_checks_user_id", "user_id"),)


class KycDocument(Base, TimestampMixin):
    """Encrypted document blob metadata. Blob lives in storage backend,
    key = storage_key. sha256 enables cross-account reuse detection."""

    __tablename__ = "kyc_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE")
    )
    doc_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kyc_document_types.id"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", create_type=False),
        nullable=False,
        server_default=DocumentStatus.PENDING.value,
    )
    rejection_reason_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("kyc_rejection_reasons.id")
    )
    version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    doc_type: Mapped["KycDocumentType"] = relationship()

    __table_args__ = (
        Index("ix_kyc_documents_owner", "owner_user_id"),
        Index("ix_kyc_documents_property", "property_id"),
        Index("ix_kyc_documents_sha256", "sha256"),
        Index("ix_kyc_documents_status", "status"),
    )


class PropertyOwnershipClaim(Base, TimestampMixin):
    """'I own/control this property' — one open claim per listing.
    Verified claim → verification_level 2 → 'document-verified owner' badge.
    We never claim title verification."""

    __tablename__ = "property_ownership_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("seller_claim_types.id"), nullable=False
    )
    status: Mapped[OwnershipClaimStatus] = mapped_column(
        Enum(OwnershipClaimStatus, name="ownership_claim_status"),
        nullable=False,
        server_default=OwnershipClaimStatus.SUBMITTED.value,
    )
    owner_name_on_doc: Mapped[str] = mapped_column(String(120), nullable=False)
    name_match_score: Mapped[int | None] = mapped_column(SmallInteger)
    address_match_score: Mapped[int | None] = mapped_column(SmallInteger)
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    claim_type: Mapped["SellerClaimType"] = relationship()
    property: Mapped["Property"] = relationship(back_populates="ownership_claims")

    __table_args__ = (
        Index("ix_ownership_claims_property", "property_id"),
        Index("ix_ownership_claims_status", "status"),
    )


class KycReview(Base):
    """Append-only reviewer/admin decision log (incl. doc-view audits)."""

    __tablename__ = "kyc_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_type: Mapped[KycSubjectType] = mapped_column(
        Enum(KycSubjectType, name="kyc_subject_type", create_type=False),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reviewer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    decision: Mapped[ReviewDecision] = mapped_column(
        Enum(ReviewDecision, name="review_decision"), nullable=False
    )
    reason_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("kyc_rejection_reasons.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_kyc_reviews_subject", "subject_type", "subject_id"),
        Index("ix_kyc_reviews_reviewer", "reviewer_id"),
    )


class KycStatusHistory(Base):
    """Every state transition on profiles/claims, with actor."""

    __tablename__ = "kyc_status_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_type: Mapped[KycSubjectType] = mapped_column(
        Enum(KycSubjectType, name="kyc_subject_type", create_type=False),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(40))
    new_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_kyc_status_history_subject", "subject_type", "subject_id"),
    )


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(20), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_consent_records_user", "user_id", "purpose"),
    )
