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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import (
    AppealStatus,
    DuplicateMatchStatus,
    RiskEventType,
)


class UserRiskEvent(Base):
    """One scored broker-risk event. dedup_key prevents double-scoring."""

    __tablename__ = "user_risk_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[RiskEventType] = mapped_column(
        Enum(RiskEventType, name="risk_event_type"), nullable=False
    )
    score_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    property_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="SET NULL")
    )
    related_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    report_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("property_reports.id", ondelete="SET NULL")
    )
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_user_risk_events_user_id", "user_id"),
        Index("ix_user_risk_events_created_at", "created_at"),
    )


class FraudConfig(Base):
    """Admin-editable rule scores and thresholds."""

    __tablename__ = "fraud_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DuplicateMatch(Base):
    """Candidate duplicate/similar listing detected by the broker engine."""

    __tablename__ = "duplicate_matches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    matched_property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    matched_owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    signals: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    status: Mapped[DuplicateMatchStatus] = mapped_column(
        Enum(DuplicateMatchStatus, name="duplicate_match_status"),
        nullable=False,
        server_default=DuplicateMatchStatus.UNREVIEWED.value,
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "property_id", "matched_property_id", name="uq_duplicate_pair"
        ),
        Index("ix_duplicate_matches_status", "status"),
    )


class Appeal(Base):
    """User appeal against a restriction/suspension/moderation action."""

    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="SET NULL")
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AppealStatus] = mapped_column(
        Enum(AppealStatus, name="appeal_status"),
        nullable=False,
        server_default=AppealStatus.OPEN.value,
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_appeals_user_id", "user_id"),
        Index("ix_appeals_status", "status"),
    )


class AdminAuditLog(Base):
    """Immutable record of every admin/system moderation action."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_admin_audit_logs_admin_id", "admin_id"),
        Index("ix_admin_audit_logs_target", "target_type", "target_id"),
        Index("ix_admin_audit_logs_created_at", "created_at"),
    )
