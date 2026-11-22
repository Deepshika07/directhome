"""Subscriptions, plans & entitlements — Phase 1 (mock billing).

Entitlement model: a user's effective plan = their ACTIVE subscription's
plan, else the FREE_* plan for that role. Entitlements live in
plan.features JSONB — known keys:
    max_active_listings  (seller) int|null  concurrent live listings
    contact_reveals      (buyer)  int|null  distinct properties per window —
                                repeat reveals on a contacted listing are free
null = unlimited. Usage window = subscription window; free-plan users count
all-time. Everything is checked in the service layer (like the L1 gate).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request, not_found
from app.models import (
    PaymentOrder,
    Plan,
    Property,
    PropertyInquiry,
    User,
    UserSubscription,
)
from app.services.audit_service import log_admin_action
from app.services.providers.billing_provider import get_billing_provider

LIVE_LISTING_STATUSES = ("PENDING_REVIEW", "ACTIVE", "PAUSED", "FLAGGED")


def _v(x):
    return x.value if hasattr(x, "value") else x


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- plan resolution & expiry ----------------

def _expire_if_needed(sub: UserSubscription) -> None:
    if (_v(sub.status) == "ACTIVE" and sub.expires_at
            and sub.expires_at <= _now()):
        sub.status = "EXPIRED"


def expire_stale(db: Session, user_id: int | None = None) -> None:
    """Persist EXPIRED on ACTIVE subs past expires_at. Called on read paths
    so admin views/stats see truthful statuses. Commits — every call site
    is write-free at that point."""
    q = (update(UserSubscription)
         .where(UserSubscription.status == "ACTIVE",
                UserSubscription.expires_at.is_not(None),
                UserSubscription.expires_at <= _now())
         .values(status="EXPIRED"))
    if user_id is not None:
        q = q.where(UserSubscription.user_id == user_id)
    db.execute(q)
    db.commit()


def active_subscription(db: Session, user: User,
                        applies_to: str) -> UserSubscription | None:
    expire_stale(db, user.id)
    return db.scalar(
        select(UserSubscription)
        .join(Plan, Plan.id == UserSubscription.plan_id)
        .where(UserSubscription.user_id == user.id,
               UserSubscription.status == "ACTIVE",
               Plan.applies_to == applies_to)
        .order_by(UserSubscription.starts_at.desc()))


def effective_plan(db: Session, user: User, applies_to: str) -> tuple[Plan, UserSubscription | None]:
    sub = active_subscription(db, user, applies_to)
    if sub:
        return sub.plan, sub
    free = db.scalar(select(Plan).where(
        Plan.applies_to == applies_to,
        Plan.code == f"FREE_{applies_to}", Plan.is_active.is_(True)))
    if free is None:
        raise ApiError(403, "PLAN_REQUIRED",
                       "No active plan — choose a subscription to continue")
    return free, None


# ---------------- entitlements ----------------

def _window_start(db: Session, user: User,
                  sub: UserSubscription | None) -> datetime | None:
    return sub.starts_at if sub else None


def listings_used(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count(Property.id)).where(
            Property.owner_id == user.id,
            Property.status.in_(LIVE_LISTING_STATUSES))
    ) or 0


def contact_reveals_used(db: Session, user: User,
                         since: datetime | None) -> int:
    """Distinct properties contacted — a repeat reveal on the same listing
    is free and never consumes another credit."""
    q = select(func.count(PropertyInquiry.property_id.distinct())).where(
        PropertyInquiry.buyer_id == user.id)
    if since:
        q = q.where(PropertyInquiry.created_at >= since)
    return db.scalar(q) or 0


def _already_revealed(db: Session, user: User, property_id: int) -> bool:
    """Buyer has a prior inquiry on THIS property (any window) — re-reveals
    on the same listing are free."""
    return db.scalar(
        select(PropertyInquiry.id).where(
            PropertyInquiry.buyer_id == user.id,
            PropertyInquiry.property_id == property_id).limit(1)) is not None


def check_contact_reveal(db: Session, user: User, property_id: int) -> None:
    """Gate: revealing the contact on a listing. Free if this property was
    already revealed; else raises LIMIT_REACHED when the window quota is
    spent. Same owner + different property consumes another credit."""
    if _already_revealed(db, user, property_id):
        return
    plan, sub = effective_plan(db, user, "BUYER")
    limit = plan.features.get("contact_reveals")
    if limit is not None:
        used = contact_reveals_used(db, user, _window_start(db, user, sub))
        if used >= limit:
            raise ApiError(
                403, "LIMIT_REACHED",
                f"Plan '{plan.name}' includes {limit} owner contacts — "
                "upgrade for more")


def check_listing_slot(db: Session, user: User) -> None:
    """Gate: submitting a listing. Raises LIMIT_REACHED when the plan's
    max_active_listings is exhausted."""
    plan, sub = effective_plan(db, user, "SELLER")
    limit = plan.features.get("max_active_listings")
    if limit is not None and listings_used(db, user) >= limit:
        raise ApiError(
            403, "LIMIT_REACHED",
            f"Plan '{plan.name}' allows {limit} live listing(s) — "
            "upgrade for more")


def usage_snapshot(db: Session, user: User) -> dict:
    """What /billing/me reports for both roles."""
    out = {}
    for role, key, used_fn in (
        ("SELLER", "max_active_listings",
         lambda: listings_used(db, user)),
        ("BUYER", "contact_reveals",
         lambda: contact_reveals_used(
             db, user, _window_start(db, user,
                                     active_subscription(db, user, "BUYER")))),
    ):
        plan, sub = effective_plan(db, user, role)
        limit = plan.features.get(key)
        used = used_fn()
        out[role.lower()] = {
            "plan_code": plan.code, "plan_name": plan.name,
            "subscribed": sub is not None,
            "limit": limit, "used": used,
            "expires_at": sub.expires_at if sub else None,
        }
    return out


# ---------------- orders & activation ----------------

def create_order(db: Session, user: User, plan_id: int) -> dict:
    plan = db.get(Plan, plan_id)
    if plan is None or not plan.is_active:
        raise not_found("PLAN_NOT_FOUND", "Plan not found")

    order = PaymentOrder(user_id=user.id, plan_id=plan.id,
                         amount_paise=plan.price_paise,
                         currency=plan.currency)
    db.add(order)
    db.flush()

    if plan.price_paise == 0:
        order.status = "PAID"
        order.paid_at = _now()
        sub = _activate(db, user, plan, order)
        db.commit()
        return {"order_id": order.id, "status": "PAID",
                "subscription_id": sub.id}

    provider = get_billing_provider()
    res = provider.create_order(
        order_id=order.id, amount_paise=order.amount_paise,
        currency=order.currency, description=f"{plan.name} — {plan.applies_to}")
    order.provider = provider.name
    order.provider_ref = res.provider_ref
    db.commit()
    return {"order_id": order.id, "status": "CREATED",
            "checkout": res.client_payload}


def confirm_order(db: Session, user: User, order_id: int,
                  signature: str | None) -> dict:
    order = db.get(PaymentOrder, order_id)
    if order is None or order.user_id != user.id:
        raise not_found("ORDER_NOT_FOUND", "Order not found")
    if _v(order.status) != "CREATED":
        raise bad_request("ORDER_CLOSED",
                          f"Order already {_v(order.status).lower()}")

    provider = get_billing_provider()
    if not provider.verify_payment(provider_ref=order.provider_ref or "",
                                   signature=signature):
        order.status = "FAILED"
        db.commit()
        raise bad_request("PAYMENT_FAILED", "Payment verification failed")

    order.status = "PAID"
    order.paid_at = _now()
    sub = _activate(db, user, order.plan, order)
    db.commit()
    return {"order_id": order.id, "status": "PAID",
            "subscription_id": sub.id}


def _activate(db: Session, user: User, plan: Plan,
              order: PaymentOrder | None) -> UserSubscription:
    """Activate plan for user; replaces any ACTIVE sub for the same scope."""
    for old in db.scalars(
            select(UserSubscription)
            .join(Plan, Plan.id == UserSubscription.plan_id)
            .where(UserSubscription.user_id == user.id,
                   UserSubscription.status == "ACTIVE",
                   Plan.applies_to == plan.applies_to)):
        _expire_if_needed(old)
        if _v(old.status) == "ACTIVE":
            old.status = "CANCELLED"
    sub = UserSubscription(
        user_id=user.id, plan_id=plan.id, source="PAYMENT",
        expires_at=(_now() + timedelta(days=plan.duration_days)
                    if plan.duration_days else None),
        payment_order_id=order.id if order else None,
    )
    db.add(sub)
    db.flush()
    return sub


# ---------------- admin ----------------

def grant_plan(db: Session, admin: User, user_id: int, plan_id: int,
               days: int | None) -> UserSubscription:
    plan = db.get(Plan, plan_id)
    target = db.get(User, user_id)
    if plan is None or target is None:
        raise not_found("NOT_FOUND", "Unknown user or plan")
    # one ACTIVE sub per scope — cancel any current one first
    for old in db.scalars(
            select(UserSubscription)
            .join(Plan, Plan.id == UserSubscription.plan_id)
            .where(UserSubscription.user_id == target.id,
                   UserSubscription.status == "ACTIVE",
                   Plan.applies_to == plan.applies_to)):
        _expire_if_needed(old)
        if _v(old.status) == "ACTIVE":
            old.status = "CANCELLED"
    if days is not None:
        expires = _now() + timedelta(days=days)
    elif plan.duration_days:
        expires = _now() + timedelta(days=plan.duration_days)
    else:
        expires = None
    sub = UserSubscription(
        user_id=target.id, plan_id=plan.id, source="ADMIN_GRANT",
        expires_at=expires,
    )
    db.add(sub)
    db.flush()
    log_admin_action(db, admin, "PLAN_GRANT", "user", target.id,
                     extra={"plan": plan.code})
    db.commit()
    return sub


def revoke_subscription(db: Session, admin: User, sub_id: int) -> dict:
    sub = db.get(UserSubscription, sub_id)
    if sub is None:
        raise not_found("SUB_NOT_FOUND", "Subscription not found")
    if _v(sub.status) != "ACTIVE":
        raise bad_request("SUB_CLOSED", "Subscription is not active")
    sub.status = "CANCELLED"
    log_admin_action(db, admin, "PLAN_REVOKE", "user", sub.user_id,
                     extra={"plan": sub.plan.code, "subscription_id": sub.id})
    db.commit()
    return {"id": sub.id, "status": "CANCELLED"}


def stats(db: Session) -> dict:
    expire_stale(db)
    paid = select(func.count(PaymentOrder.id),
                  func.coalesce(func.sum(PaymentOrder.amount_paise), 0)
                  ).where(PaymentOrder.status == "PAID")
    orders, revenue = db.execute(paid).one()
    subs_by_plan = db.execute(
        select(Plan.code, func.count(UserSubscription.id))
        .join(UserSubscription, UserSubscription.plan_id == Plan.id)
        .where(UserSubscription.status == "ACTIVE")
        .group_by(Plan.code)).all()
    expiring = db.scalar(
        select(func.count(UserSubscription.id)).where(
            UserSubscription.status == "ACTIVE",
            UserSubscription.expires_at.is_not(None),
            UserSubscription.expires_at <= _now() + timedelta(days=7))) or 0
    return {
        "paid_orders": orders, "revenue_paise": revenue,
        "active_subscriptions_by_plan": dict(subs_by_plan),
        "expiring_next_7_days": expiring,
    }
