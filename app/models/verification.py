from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    AvailabilityEventStatus,
    ConfirmationMethod,
    DocumentStatus,
    VerificationStatus,
    VerificationType,
)


class VerificationDocument(Base, TimestampMixin):
    __tablename__ = "verification_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE")
    )
    document_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_types.id"), nullable=False
    )
    file_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        server_default=DocumentStatus.PENDING.value,
    )
    verified_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    document_type: Mapped["DocumentType"] = relationship()

    __table_args__ = (
        Index("ix_verification_documents_user_id", "user_id"),
        Index("ix_verification_documents_property_id", "property_id"),
        Index("ix_verification_documents_status", "status"),
    )


class PropertyVerification(Base):
    __tablename__ = "property_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    verification_type: Mapped[VerificationType] = mapped_column(
        Enum(VerificationType, name="verification_type"), nullable=False
    )
    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"),
        nullable=False,
        server_default=VerificationStatus.PENDING.value,
    )
    verified_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_property_verifications_property_id", "property_id"),
        Index("ix_property_verifications_status", "status"),
    )


class PropertyStatusHistory(Base):
    __tablename__ = "property_status_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[str | None] = mapped_column(String(50))
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_property_status_history_property_id", "property_id"),
    )


class PropertyAvailabilityHistory(Base):
    __tablename__ = "property_availability_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AvailabilityEventStatus] = mapped_column(
        Enum(AvailabilityEventStatus, name="availability_event_status"),
        nullable=False,
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmation_method: Mapped[ConfirmationMethod | None] = mapped_column(
        Enum(ConfirmationMethod, name="confirmation_method")
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_property_availability_history_property_id", "property_id"),
        Index("ix_property_availability_history_confirmed_at", "confirmed_at"),
    )
