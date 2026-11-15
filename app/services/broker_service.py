"""BrokerDetectionService — rule-based risk scoring for anti-broker detection.

Scores live in the fraud_config table (admin-editable) and fall back to
DEFAULTS. Every scored event is stored in user_risk_events with a unique
dedup_key, so the same event can never be scored twice. Account status is
escalated automatically at configured score bands — de-escalation is always
a manual admin action, and nothing ever auto-bans.
"""

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    DuplicateMatch,
    FraudConfig,
    Property,
    PropertyLocation,
    PropertyReport,
    PropertyStatusHistory,
    User,
    UserRiskEvent,
    enums,
)
from app.services.audit_service import log_admin_action

# ---- configurable scores / thresholds (seeded into fraud_config) ----

DEFAULTS = {
    "score.unrelated_listings": 30,
    "score.multi_account_phone": 40,
    "score.cross_account_duplicate_property": 35,
    "score.repeated_content": 20,
    "score.ownership_mismatch": 50,
    "score.validated_complaints": 40,
    "threshold.flag_for_review": 30,
    "threshold.restrict": 50,
    "threshold.suspend": 70,
    "listing.unrelated_count": 5,
    "report.flag_threshold": 3,
    "report.validated_threshold": 2,
    "content.similarity_pct": 85,
    "listing.require_verified_channel": 0,
    "terms.current_version": 0,  # unused numerically; version kept in settings below
}

CURRENT_TERMS_VERSION = "1.0"

FRAUD_REPORT_REASONS = {
    "BROKER", "FAKE_LISTING", "INCORRECT_OWNERSHIP", "SCAM", "DUPLICATE",
}

# statuses that still count as "live-ish" inventory for the rules
LIVE_STATUSES = {"ACTIVE", "PENDING_REVIEW", "FLAGGED", "PAUSED"}

_SEVERITY = {
    "ACTIVE": 0,
    "UNDER_REVIEW": 1,
    "TEMPORARILY_RESTRICTED": 2,
    "SUSPENDED": 3,
    "PERMANENTLY_BANNED": 4,
}

PUBLISH_BLOCKING = {
    "TEMPORARILY_RESTRICTED", "SUSPENDED", "PERMANENTLY_BANNED",
}


def _v(x):
    return x.value if hasattr(x, "value") else x


def get_config(db: Session) -> dict:
    cfg = dict(DEFAULTS)
    for row in db.scalars(select(FraudConfig)).all():
        cfg[row.key] = row.value
    return cfg


def band_for(score: int, cfg: dict) -> str | None:
    """Score band → account status (None = normal)."""
    if score >= cfg["threshold.suspend"]:
        return "SUSPENDED"
    if score >= cfg["threshold.restrict"]:
        return "TEMPORARILY_RESTRICTED"
    if score >= cfg["threshold.flag_for_review"]:
        return "UNDER_REVIEW"
    return None


# ---------------- event recording & recalculation ----------------

def record_event(
    db: Session,
    *,
    user: User,
    event_type,
    delta: int,
    dedup_key: str,
    reason: str | None = None,
    property_id: int | None = None,
    related_user_id: int | None = None,
    report_id: int | None = None,
) -> UserRiskEvent | None:
    """Store a scored event (skipped if dedup_key seen) and recalculate."""
    exists = db.scalar(
        select(UserRiskEvent.id).where(UserRiskEvent.dedup_key == dedup_key)
    )
    if exists:
        return None
    ev = UserRiskEvent(
        user_id=user.id, event_type=event_type, score_delta=delta,
        reason=reason, property_id=property_id,
        related_user_id=related_user_id, report_id=report_id,
        dedup_key=dedup_key,
    )
    db.add(ev)
    db.flush()
    recalculate(db, user)
    return ev


def recalculate(db: Session, user: User) -> None:
    """Recompute risk_score from events and auto-escalate status (never down)."""
    cfg = get_config(db)
    score = db.scalar(
        select(func.coalesce(func.sum(UserRiskEvent.score_delta), 0)).where(
            UserRiskEvent.user_id == user.id
        )
    ) or 0
    user.risk_score = score
    target = band_for(score, cfg)
    current = _v(user.account_status)
    if target and _SEVERITY[target] > _SEVERITY[current]:
        user.account_status = target
        user.account_status_reason = (
            f"Auto-escalated: risk score reached {score}"
        )
        user.account_status_changed_at = datetime.now(timezone.utc)
        log_admin_action(
            db, None, f"SYSTEM_ACCOUNT_{target}", "user", user.id,
            f"risk_score={score}",
        )
    db.flush()


# ---------------- normalization & similarity ----------------

_norm_re = re.compile(r"[^a-z0-9]+")


def _norm(s: str | None) -> str:
    return _norm_re.sub("", (s or "").lower())


def address_fingerprint(loc: PropertyLocation) -> str:
    """Normalized address hash — ignores punctuation, spacing, case, flat-no. format."""
    parts = [
        _norm(loc.address_line1), _norm(loc.locality),
        _norm(loc.city), _norm(loc.pincode),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:32]


def _record_match(
    db: Session, prop: Property, other: Property, score: int, signals: dict
) -> None:
    lo, hi = sorted((prop.id, other.id))
    exists = db.scalar(
        select(DuplicateMatch.id).where(
            DuplicateMatch.property_id == lo,
            DuplicateMatch.matched_property_id == hi,
        )
    )
    if exists:
        return
    db.add(
        DuplicateMatch(
            property_id=lo, matched_property_id=hi,
            matched_owner_id=other.owner_id, score=score, signals=signals,
        )
    )


# ---------------- rule checks ----------------

def on_property_submitted(db: Session, user: User, prop: Property) -> None:
    """Run broker rules when a listing enters PENDING_REVIEW. Caller commits."""
    cfg = get_config(db)
    if prop.location:
        prop.address_fingerprint = address_fingerprint(prop.location)
    _check_listing_spread(db, user, cfg)
    _check_shared_phone(db, user, cfg)
    _check_cross_account_duplicates(db, user, prop, cfg)
    _check_repeated_content(db, user, prop, cfg)
    db.flush()


def _check_listing_spread(db: Session, user: User, cfg: dict) -> None:
    """5+ active listings in unrelated localities."""
    groups = db.scalar(
        select(
            func.count(
                func.distinct(
                    func.concat(
                        func.lower(PropertyLocation.locality),
                        "|",
                        func.lower(PropertyLocation.city),
                    )
                )
            )
        )
        .select_from(Property)
        .join(PropertyLocation, PropertyLocation.property_id == Property.id)
        .where(Property.owner_id == user.id, Property.status.in_(LIVE_STATUSES))
    ) or 0
    if groups >= cfg["listing.unrelated_count"]:
        record_event(
            db, user=user, event_type=enums.RiskEventType.UNRELATED_LISTINGS,
            delta=cfg["score.unrelated_listings"],
            dedup_key=f"spread:{user.id}",
            reason=f"{groups} listings across distinct localities",
        )


def _check_shared_phone(db: Session, user: User, cfg: dict) -> None:
    """Same phone on multiple accounts (dormant while phone stays unique)."""
    count = db.scalar(
        select(func.count(User.id)).where(
            User.phone == user.phone, User.id != user.id
        )
    ) or 0
    if count:
        record_event(
            db, user=user, event_type=enums.RiskEventType.MULTI_ACCOUNT_PHONE,
            delta=cfg["score.multi_account_phone"],
            dedup_key=f"shared-phone:{user.id}",
            reason=f"phone shared with {count} other account(s)",
        )


def _check_cross_account_duplicates(
    db: Session, user: User, prop: Property, cfg: dict
) -> None:
    """Same normalized address listed under a different account."""
    if not prop.address_fingerprint:
        return
    others = db.scalars(
        select(Property).where(
            Property.address_fingerprint == prop.address_fingerprint,
            Property.id != prop.id,
            Property.status.in_(LIVE_STATUSES),
        )
    ).all()
    for o in others:
        if o.owner_id == user.id:
            _record_match(
                db, prop, o, 60, {"same_address": True, "same_owner": True}
            )
        else:
            _record_match(
                db, prop, o, 80,
                {"same_address": True, "different_owner": True},
            )
            record_event(
                db, user=user,
                event_type=enums.RiskEventType.CROSS_ACCOUNT_DUPLICATE_PROPERTY,
                delta=cfg["score.cross_account_duplicate_property"],
                dedup_key=f"xprop:{prop.id}:{o.id}",
                property_id=prop.id, related_user_id=o.owner_id,
                reason=f"same address as property {o.id} (owner {o.owner_id})",
            )


def _check_repeated_content(
    db: Session, user: User, prop: Property, cfg: dict
) -> None:
    """Reused images or near-identical title/description across the account."""
    others = db.scalars(
        select(Property).where(
            Property.owner_id == user.id,
            Property.id != prop.id,
            Property.status.in_(LIVE_STATUSES),
        )
    ).all()
    prop_images = {m.url for m in prop.media if m.media_type == "IMAGE"}
    for o in others:
        other_images = {m.url for m in o.media if m.media_type == "IMAGE"}
        shared = prop_images & other_images
        sim = 0.0
        if not shared:
            sim = max(
                SequenceMatcher(None, prop.title or "", o.title or "").ratio(),
                SequenceMatcher(
                    None, prop.description or "", o.description or ""
                ).ratio(),
            )
        if shared or sim * 100 >= cfg["content.similarity_pct"]:
            signals = (
                {"shared_image_urls": sorted(shared)}
                if shared
                else {"text_similarity": round(sim, 2)}
            )
            _record_match(db, prop, o, 40, signals)
            record_event(
                db, user=user, event_type=enums.RiskEventType.REPEATED_CONTENT,
                delta=cfg["score.repeated_content"],
                dedup_key=f"content:{prop.id}:{o.id}",
                property_id=prop.id,
                reason=f"reused content matching property {o.id}",
            )


def on_document_rejected(
    db: Session, doc, reason: str | None, ownership_mismatch: bool
) -> None:
    """Admin flagged an ownership-document mismatch → heavy score. Caller commits."""
    if not ownership_mismatch or doc.user is None:
        return
    cfg = get_config(db)
    record_event(
        db, user=doc.user, event_type=enums.RiskEventType.OWNERSHIP_MISMATCH,
        delta=cfg["score.ownership_mismatch"],
        dedup_key=f"mismatch:{doc.id}",
        property_id=doc.property_id,
        reason=reason or "ownership details mismatch",
    )


def on_report_created(db: Session, report: PropertyReport) -> None:
    """Flag a listing once OPEN reports cross the configured threshold.

    Flags the LISTING only — the owner is scored only if reports get
    validated by an admin (on_report_validated). Caller commits.
    """
    cfg = get_config(db)
    open_count = db.scalar(
        select(func.count(PropertyReport.id)).where(
            PropertyReport.property_id == report.property_id,
            PropertyReport.status == "OPEN",
        )
    ) or 0
    if open_count < cfg["report.flag_threshold"]:
        return
    prop = db.get(Property, report.property_id)
    if prop is None or _v(prop.status) != "ACTIVE":
        return
    old = _v(prop.status)
    prop.status = "FLAGGED"
    db.add(
        PropertyStatusHistory(
            property_id=prop.id, old_status=old, new_status="FLAGGED",
            changed_by=None, reason=f"{open_count} open reports",
        )
    )
    log_admin_action(
        db, None, "SYSTEM_PROPERTY_FLAGGED", "property", prop.id,
        f"{open_count} open reports",
    )


def on_report_validated(db: Session, report: PropertyReport) -> None:
    """Admin resolved a report as valid → score the property owner. Caller commits."""
    if _v(report.reason) not in FRAUD_REPORT_REASONS:
        return
    prop = db.get(Property, report.property_id)
    if prop is None:
        return
    owner = db.get(User, prop.owner_id)
    if owner is None:
        return
    cfg = get_config(db)
    validated = db.scalar(
        select(func.count(PropertyReport.id))
        .join(Property, Property.id == PropertyReport.property_id)
        .where(
            Property.owner_id == owner.id,
            PropertyReport.status == "RESOLVED",
            PropertyReport.validated.is_(True),
            PropertyReport.reason.in_(list(FRAUD_REPORT_REASONS)),
        )
    ) or 0
    if validated >= cfg["report.validated_threshold"]:
        record_event(
            db, user=owner, event_type=enums.RiskEventType.VALIDATED_COMPLAINTS,
            delta=cfg["score.validated_complaints"],
            dedup_key=f"complaints:{owner.id}",
            property_id=prop.id, report_id=report.id,
            reason=f"{validated} validated fraud reports",
        )
