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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import BillingSource, PaymentStatus, SubscriptionStatus


class Plan(Base, TimestampMixin):
    """Admin-managed subscription plan. `features` JSONB is the entitlement
    map — known keys: max_active_listings (int|null, seller),
    contact_reveals (int|null, buyer), boost, priority_support. null =
    unlimited. Add new keys freely; the service reads what it knows."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    applies_to: Mapped[str] = mapped_column(String(20), nullable=False)  # SELLER|BUYER
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False,
                                           server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False,
                                          server_default="INR")
    duration_days: Mapped[int | None] = mapped_column(
        Integer)  # null = lifetime credits
    features: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )


class UserSubscription(Base, TimestampMixin):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        nullable=False, server_default=SubscriptionStatus.ACTIVE.value,
    )
    source: Mapped[BillingSource] = mapped_column(
        Enum(BillingSource, name="billing_source"),
        nullable=False, server_default=BillingSource.PAYMENT.value,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))  # null = lifetime credits
    payment_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_orders.id", ondelete="SET NULL")
    )

    plan: Mapped["Plan"] = relationship()

    __table_args__ = (
        Index("ix_user_subscriptions_user", "user_id", "status"),
        Index("ix_user_subscriptions_expires", "expires_at"),
    )


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False
    )
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False,
                                          server_default="INR")
    provider: Mapped[str] = mapped_column(String(30), nullable=False,
                                          server_default="mock")
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"),
        nullable=False, server_default=PaymentStatus.CREATED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped["Plan"] = relationship()

    __table_args__ = (Index("ix_payment_orders_user", "user_id"),)
