from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, user_role_codes
from app.core.database import get_db
from app.core.errors import bad_request, not_found
from app.models import (
    AdminAuditLog,
    Appeal,
    DuplicateMatch,
    FraudConfig,
    Property,
    PropertyReport,
    PropertyStatusHistory,
    User,
    UserRiskEvent,
    VerificationDocument,
    enums,
)
from app.schemas.antifraud import (
    AppealResolveIn,
    FraudConfigUpdateIn,
    ModerationIn,
    RestrictionIn,
)
from app.schemas.common import PageMeta, PageParams, ok
from app.services import broker_service as broker
from app.services.audit_service import log_admin_action

router = APIRouter(prefix="/admin", tags=["admin-fraud"])


def _v(x):
    return x.value if hasattr(x, "value") else x


def _user_brief(db: Session, u: User) -> dict:
    return {
        "id": u.id, "name": u.name, "phone": u.phone, "email": u.email,
        "kyc_status": _v(u.kyc_status),
        "account_status": _v(u.account_status),
        "risk_score": u.risk_score,
        "roles": user_role_codes(db, u.id),
        "created_at": u.created_at.isoformat(),
    }


def _status_history(db: Session, prop: Property, old: str, new: str,
                    admin: User, reason: str | None) -> None:
    prop.status = new
    db.add(
        PropertyStatusHistory(
            property_id=prop.id, old_status=old, new_status=new,
            changed_by=admin.id, reason=reason,
        )
    )


# ---------------- verification & moderation queues ----------------

@router.get("/verifications/pending")
def pending_verifications(
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """PENDING_REVIEW listings + owner risk/KYC context for the reviewer."""
    q = (
        select(Property)
        .where(Property.status == "PENDING_REVIEW")
        .order_by(Property.created_at.asc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    from app.services import property_service as svc

    out = []
    for p in rows:
        doc_count = db.scalar(
            select(func.count(VerificationDocument.id)).where(
                VerificationDocument.property_id == p.id,
                VerificationDocument.status == "PENDING",
            )
        ) or 0
        out.append(
            {
                **svc.card_from(p).model_dump(mode="json"),
                "owner": _user_brief(db, p.owner) if p.owner else None,
                "pending_documents": doc_count,
            }
        )
    return ok(out, PageMeta(page=page_params.page, limit=page_params.limit, total=total))


@router.get("/fraud/flagged-users")
def flagged_users(
    min_score: int = Query(default=1),
    account_status: str | None = None,
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Users ordered by risk score — the broker-watch queue."""
    q = select(User).where(
        or_(
            User.risk_score >= min_score,
            User.account_status != "ACTIVE",
        )
    )
    if account_status:
        q = q.where(User.account_status == account_status.upper())
    q = q.order_by(User.risk_score.desc(), User.account_status_changed_at.desc())
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [
            {
                **_user_brief(db, u),
                "account_status_reason": u.account_status_reason,
            }
            for u in rows
        ],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.get("/fraud/users/{user_id}/risk-history")
def user_risk_history(
    user_id: int,
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise not_found("USER_NOT_FOUND", "User not found")
    q = (
        select(UserRiskEvent)
        .where(UserRiskEvent.user_id == user_id)
        .order_by(UserRiskEvent.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        {
            "user": _user_brief(db, target),
            "events": [
                {
                    "id": e.id, "event_type": _v(e.event_type),
                    "score_delta": e.score_delta, "reason": e.reason,
                    "property_id": e.property_id,
                    "related_user_id": e.related_user_id,
                    "report_id": e.report_id,
                    "created_at": e.created_at.isoformat(),
                }
                for e in rows
            ],
        },
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.get("/fraud/reported-properties")
def reported_properties(
    status: str = Query(default="OPEN"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reports joined with property title + reporter identity."""
    q = (
        select(PropertyReport)
        .where(PropertyReport.status == status.upper())
        .order_by(PropertyReport.created_at.asc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    out = []
    for r in rows:
        prop = db.get(Property, r.property_id)
        reporter = db.get(User, r.reported_by) if r.reported_by else None
        out.append(
            {
                "id": r.id, "property_id": r.property_id,
                "property_title": prop.title if prop else None,
                "property_status": _v(prop.status) if prop else None,
                "reporter_name": reporter.name if reporter else None,
                "reason": _v(r.reason), "description": r.description,
                "status": _v(r.status),
                "created_at": r.created_at.isoformat(),
            }
        )
    return ok(out, PageMeta(page=page_params.page, limit=page_params.limit, total=total))


@router.get("/fraud/duplicate-listings")
def duplicate_listings(
    status: str = Query(default="UNREVIEWED"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Duplicate/similar listing pairs found by the detection engine."""
    q = (
        select(DuplicateMatch)
        .where(DuplicateMatch.status == status.upper())
        .order_by(DuplicateMatch.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    out = []
    for m in rows:
        a = db.get(Property, m.property_id)
        b = db.get(Property, m.matched_property_id)
        out.append(
            {
                "id": m.id,
                "property_id": m.property_id,
                "property_title": a.title if a else None,
                "property_owner_id": a.owner_id if a else None,
                "matched_property_id": m.matched_property_id,
                "matched_title": b.title if b else None,
                "matched_owner_id": b.owner_id if b else None,
                "score": m.score, "signals": m.signals,
                "status": _v(m.status),
                "created_at": m.created_at.isoformat(),
            }
        )
    return ok(out, PageMeta(page=page_params.page, limit=page_params.limit, total=total))


@router.post("/fraud/duplicate-matches/{match_id}/confirm")
def confirm_duplicate(
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    m = db.get(DuplicateMatch, match_id)
    if m is None:
        raise not_found("MATCH_NOT_FOUND", "Duplicate match not found")
    m.status = "CONFIRMED"
    m.reviewed_by = admin.id
    m.reviewed_at = datetime.now(timezone.utc)
    log_admin_action(db, admin, "DUPLICATE_CONFIRMED", "duplicate_match", m.id)
    db.commit()
    return ok({"message": "Duplicate match confirmed"})


@router.post("/fraud/duplicate-matches/{match_id}/dismiss")
def dismiss_duplicate(
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    m = db.get(DuplicateMatch, match_id)
    if m is None:
        raise not_found("MATCH_NOT_FOUND", "Duplicate match not found")
    m.status = "DISMISSED"
    m.reviewed_by = admin.id
    m.reviewed_at = datetime.now(timezone.utc)
    log_admin_action(db, admin, "DUPLICATE_DISMISSED", "duplicate_match", m.id)
    db.commit()
    return ok({"message": "Duplicate match dismissed"})


# ---------------- property moderation ----------------

@router.post("/properties/{property_id}/moderation")
def moderate_property(
    property_id: int,
    payload: ModerationIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """UNFLAG (FLAGGED→ACTIVE) · SUSPEND (ACTIVE/FLAGGED→SUSPENDED) · REINSTATE (SUSPENDED→ACTIVE)."""
    prop = db.get(Property, property_id)
    if prop is None:
        raise not_found("PROPERTY_NOT_FOUND", "Property not found")
    action = payload.action.upper()
    current = _v(prop.status)
    moves = {
        "UNFLAG": ({"FLAGGED"}, "ACTIVE"),
        "SUSPEND": ({"ACTIVE", "FLAGGED"}, "SUSPENDED"),
        "REINSTATE": ({"SUSPENDED"}, "ACTIVE"),
    }
    if action not in moves:
        raise bad_request("INVALID_ACTION", "action must be UNFLAG|SUSPEND|REINSTATE")
    allowed_from, new_status = moves[action]
    if current not in allowed_from:
        raise bad_request(
            "INVALID_STATUS_TRANSITION",
            f"Cannot {action} a listing in status {current}",
        )
    _status_history(db, prop, current, new_status, admin, payload.reason)
    log_admin_action(
        db, admin, f"PROPERTY_{action}", "property", prop.id, payload.reason
    )
    db.commit()
    return ok({"property_id": prop.id, "status": _v(prop.status)})


# ---------------- account restriction ----------------

@router.post("/users/{user_id}/restriction")
def restrict_user(
    user_id: int,
    payload: RestrictionIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """RESTRICT · SUSPEND · UNSUSPEND · BAN an account."""
    target = db.get(User, user_id)
    if target is None:
        raise not_found("USER_NOT_FOUND", "User not found")
    if target.id == admin.id:
        raise bad_request("SELF_ACTION", "You cannot restrict your own account")
    action = payload.action.upper()
    moves = {
        "RESTRICT": "TEMPORARILY_RESTRICTED",
        "SUSPEND": "SUSPENDED",
        "UNSUSPEND": "ACTIVE",
        "BAN": "PERMANENTLY_BANNED",
    }
    if action not in moves:
        raise bad_request("INVALID_ACTION", "action must be RESTRICT|SUSPEND|UNSUSPEND|BAN")
    old = _v(target.account_status)
    target.account_status = moves[action]
    target.account_status_reason = payload.reason
    target.account_status_changed_at = datetime.now(timezone.utc)
    log_admin_action(
        db, admin, f"ACCOUNT_{action}", "user", target.id, payload.reason,
        {"old_status": old, "new_status": moves[action]},
    )
    db.commit()
    return ok({"user_id": target.id, "account_status": _v(target.account_status)})


# ---------------- appeals ----------------

@router.get("/appeals")
def list_appeals(
    status: str = Query(default="OPEN"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = (
        select(Appeal)
        .where(Appeal.status == status.upper())
        .order_by(Appeal.created_at.asc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    out = []
    for a in rows:
        u = db.get(User, a.user_id)
        out.append(
            {
                "id": a.id, "user_id": a.user_id,
                "user_name": u.name if u else None,
                "user_account_status": _v(u.account_status) if u else None,
                "property_id": a.property_id,
                "message": a.message, "status": _v(a.status),
                "created_at": a.created_at.isoformat(),
            }
        )
    return ok(out, PageMeta(page=page_params.page, limit=page_params.limit, total=total))


@router.post("/appeals/{appeal_id}/resolve")
def resolve_appeal(
    appeal_id: int,
    payload: AppealResolveIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Approve → account back to ACTIVE. Reject → restriction stands."""
    appeal = db.get(Appeal, appeal_id)
    if appeal is None:
        raise not_found("APPEAL_NOT_FOUND", "Appeal not found")
    if _v(appeal.status) != "OPEN":
        raise bad_request("ALREADY_REVIEWED", "Appeal is not open")
    appeal.status = "APPROVED" if payload.approved else "REJECTED"
    appeal.reviewed_by = admin.id
    appeal.reviewed_at = datetime.now(timezone.utc)
    appeal.resolution_note = payload.note
    user = db.get(User, appeal.user_id)
    if payload.approved and user is not None:
        user.account_status = "ACTIVE"
        user.account_status_reason = f"Appeal {appeal.id} approved"
        user.account_status_changed_at = datetime.now(timezone.utc)
    log_admin_action(
        db, admin, f"APPEAL_{_v(appeal.status)}", "appeal", appeal.id, payload.note
    )
    db.commit()
    return ok({"id": appeal.id, "status": _v(appeal.status)})


# ---------------- fraud engine config ----------------

@router.get("/fraud/config")
def get_fraud_config(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    cfg = broker.get_config(db)
    return ok(
        [
            {"key": k, "value": v, "is_default": db.get(FraudConfig, k) is None}
            for k, v in sorted(cfg.items())
        ]
    )


@router.post("/fraud/config")
def update_fraud_config(
    payload: FraudConfigUpdateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update rule scores/thresholds — applies to new evaluations immediately."""
    changed = {}
    for key, value in payload.updates.items():
        if key not in broker.DEFAULTS:
            raise bad_request("UNKNOWN_CONFIG_KEY", f"Unknown key: {key}")
        row = db.get(FraudConfig, key)
        if row is None:
            row = FraudConfig(key=key, value=value,
                              description=f"updated to {value}")
            db.add(row)
        else:
            row.value = value
        row.updated_by = admin.id
        row.updated_at = datetime.now(timezone.utc)
        changed[key] = value
    log_admin_action(db, admin, "FRAUD_CONFIG_UPDATED", "fraud_config", "*",
                     extra=changed)
    db.commit()
    return ok({"updated": changed})


# ---------------- audit trail ----------------

@router.get("/audit-logs")
def audit_logs(
    action: str | None = None,
    target_type: str | None = None,
    admin_id: int | None = None,
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = select(AdminAuditLog)
    if action:
        q = q.where(AdminAuditLog.action == action.upper())
    if target_type:
        q = q.where(AdminAuditLog.target_type == target_type)
    if admin_id:
        q = q.where(AdminAuditLog.admin_id == admin_id)
    q = q.order_by(AdminAuditLog.created_at.desc())
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    out = []
    for r in rows:
        actor = db.get(User, r.admin_id) if r.admin_id else None
        out.append(
            {
                "id": r.id,
                "admin_id": r.admin_id,
                "admin_name": actor.name if actor else "SYSTEM",
                "action": r.action, "target_type": r.target_type,
                "target_id": r.target_id, "reason": r.reason,
                "extra": r.extra,
                "created_at": r.created_at.isoformat(),
            }
        )
    return ok(out, PageMeta(page=page_params.page, limit=page_params.limit, total=total))
