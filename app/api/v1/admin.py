from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, user_role_codes
from app.core.database import get_db
from app.core.errors import bad_request, not_found
from app.models import (
    Property,
    PropertyReport,
    User,
    VerificationDocument,
    enums,
)
from app.schemas.antifraud import (
    DismissReportIn,
    DocRejectIn,
    RequestInfoIn,
    ResolveReportIn,
)
from app.schemas.common import PageMeta, PageParams, ok
from app.schemas.misc import DocumentOut
from app.schemas.property import RejectIn
from app.services import broker_service as broker
from app.services import property_service as svc
from app.services.audit_service import log_admin_action

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------- listing moderation ----------------

@router.get("/properties")
def review_queue(
    status: str = Query(default="PENDING_REVIEW"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Listings by status — default is the pending-approval queue."""
    q = (
        select(Property)
        .where(Property.status == status.upper())
        .order_by(Property.created_at.asc())
        .options(*svc._eager())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [svc.card_from(p).model_dump(mode="json") for p in rows],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.post("/properties/{property_id}/approve")
def approve_property(
    property_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """PENDING_REVIEW → ACTIVE (listing goes live)."""
    prop = svc.get_property(db, property_id)
    prop = svc.transition(db, prop, "approve", admin)
    log_admin_action(db, admin, "PROPERTY_APPROVED", "property", prop.id)
    db.commit()
    return ok(svc.detail_from(prop).model_dump(mode="json"))


@router.post("/properties/{property_id}/reject")
def reject_property(
    property_id: int,
    payload: RejectIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """PENDING_REVIEW → REJECTED with a reason."""
    prop = svc.get_property(db, property_id)
    prop = svc.transition(db, prop, "reject", admin, payload.reason)
    log_admin_action(db, admin, "PROPERTY_REJECTED", "property", prop.id,
                     payload.reason)
    db.commit()
    return ok(svc.detail_from(prop).model_dump(mode="json"))


# ---------------- KYC document review ----------------

@router.get("/documents")
def document_queue(
    status: str = Query(default="PENDING"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Submitted KYC/property documents awaiting review."""
    q = (
        select(VerificationDocument)
        .where(VerificationDocument.status == status.upper())
        .order_by(VerificationDocument.created_at.asc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [
            {
                **DocumentOut.model_validate(d).model_dump(mode="json"),
                "user_id": d.user_id,
                "user_name": d.user.name if d.user else None,
                "document_type": (
                    d.document_type.name if d.document_type else None
                ),
                "document_type_code": (
                    d.document_type.code if d.document_type else None
                ),
            }
            for d in rows
        ],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.post("/documents/{document_id}/verify")
def verify_document(
    document_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Approve a document — marks the owner's kyc_status VERIFIED for USER docs."""
    doc = db.get(VerificationDocument, document_id)
    if doc is None:
        raise not_found("DOCUMENT_NOT_FOUND", "Document not found")
    if doc.status not in {"PENDING", "REQUIRES_ADDITIONAL_INFO"}:
        raise bad_request("ALREADY_REVIEWED", "Document already decided")
    doc.status = enums.DocumentStatus.VERIFIED
    doc.verified_by = admin.id
    doc.verified_at = datetime.now(timezone.utc)
    if doc.document_type and doc.document_type.applies_to in {"USER", "BOTH"}:
        doc.user.kyc_status = enums.KycStatus.VERIFIED
    log_admin_action(db, admin, "DOCUMENT_VERIFIED", "document", doc.id)
    db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


@router.post("/documents/{document_id}/reject")
def reject_document(
    document_id: int,
    payload: DocRejectIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reject a document — owner's kyc_status becomes REJECTED.

    Pass ownership_mismatch=true when the doc contradicts declared
    ownership — it feeds the broker-risk engine.
    """
    doc = db.get(VerificationDocument, document_id)
    if doc is None:
        raise not_found("DOCUMENT_NOT_FOUND", "Document not found")
    if doc.status not in {"PENDING", "REQUIRES_ADDITIONAL_INFO"}:
        raise bad_request("ALREADY_REVIEWED", "Document already decided")
    doc.status = enums.DocumentStatus.REJECTED
    doc.verified_by = admin.id
    doc.verified_at = datetime.now(timezone.utc)
    doc.rejection_reason = payload.reason
    if doc.document_type and doc.document_type.applies_to in {"USER", "BOTH"}:
        doc.user.kyc_status = enums.KycStatus.REJECTED
    broker.on_document_rejected(
        db, doc, payload.reason, payload.ownership_mismatch
    )
    log_admin_action(
        db, admin, "DOCUMENT_REJECTED", "document", doc.id, payload.reason,
        {"ownership_mismatch": payload.ownership_mismatch},
    )
    db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


@router.post("/documents/{document_id}/request-info")
def request_document_info(
    document_id: int,
    payload: RequestInfoIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Ask the owner for additional information/document — keeps the file out
    of PENDING without rejecting it outright."""
    doc = db.get(VerificationDocument, document_id)
    if doc is None:
        raise not_found("DOCUMENT_NOT_FOUND", "Document not found")
    if doc.status != "PENDING":
        raise bad_request("ALREADY_REVIEWED", "Document is not pending")
    doc.status = enums.DocumentStatus.REQUIRES_ADDITIONAL_INFO
    doc.verified_by = admin.id
    doc.verified_at = datetime.now(timezone.utc)
    doc.notes = payload.note
    log_admin_action(
        db, admin, "DOCUMENT_INFO_REQUESTED", "document", doc.id, payload.note
    )
    db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


# ---------------- report handling ----------------

@router.get("/reports")
def report_queue(
    status: str = Query(default="OPEN"),
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Listing reports (fake/broker/scam…) awaiting review."""
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
                "reported_by": r.reported_by,
                "reporter_name": reporter.name if reporter else None,
                "reason": r.reason.value,
                "description": r.description, "status": r.status.value,
                "created_at": r.created_at.isoformat(),
            }
        )
    return ok(
        out,
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.post("/reports/{report_id}/resolve")
def resolve_report(
    report_id: int,
    payload: ResolveReportIn | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Mark a report resolved.

    validated=true (default) counts it as a confirmed fraud complaint and
    feeds the owner's risk score; pass validated=false for harmless reports.
    """
    payload = payload or ResolveReportIn()
    report = db.get(PropertyReport, report_id)
    if report is None:
        raise not_found("REPORT_NOT_FOUND", "Report not found")
    report.status = enums.ReportStatus.RESOLVED
    report.reviewed_by = admin.id
    report.reviewed_at = datetime.now(timezone.utc)
    report.validated = payload.validated
    report.resolution_note = payload.note
    if payload.validated:
        broker.on_report_validated(db, report)
    log_admin_action(
        db, admin, "REPORT_RESOLVED", "report", report.id, payload.note,
        {"validated": payload.validated},
    )
    db.commit()
    return ok({"message": "Report resolved"})


@router.post("/reports/{report_id}/dismiss")
def dismiss_report(
    report_id: int,
    payload: DismissReportIn | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Dismiss an invalid report — never affects the owner's score."""
    payload = payload or DismissReportIn()
    report = db.get(PropertyReport, report_id)
    if report is None:
        raise not_found("REPORT_NOT_FOUND", "Report not found")
    report.status = enums.ReportStatus.REJECTED
    report.reviewed_by = admin.id
    report.reviewed_at = datetime.now(timezone.utc)
    report.resolution_note = payload.note
    log_admin_action(
        db, admin, "REPORT_DISMISSED", "report", report.id, payload.note
    )
    db.commit()
    return ok({"message": "Report dismissed"})


# ---------------- user management ----------------

@router.get("/users")
def list_users(
    q: str | None = Query(default=None, description="name/phone/email fragment"),
    kyc_status: str | None = None,
    page_params: PageParams = Depends(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """User list with roles and KYC status."""
    qy = select(User)
    if q:
        like = f"%{q.strip()}%"
        qy = qy.where(
            or_(User.name.ilike(like), User.phone.ilike(like),
                User.email.ilike(like))
        )
    if kyc_status:
        qy = qy.where(User.kyc_status == kyc_status.upper())
    qy = qy.order_by(User.created_at.desc())
    total = db.scalar(select(func.count()).select_from(qy.subquery())) or 0
    rows = db.scalars(
        qy.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [
            {
                "id": u.id, "name": u.name, "phone": u.phone, "email": u.email,
                "kyc_status": u.kyc_status.value, "is_active": u.is_active,
                "roles": user_role_codes(db, u.id),
                "created_at": u.created_at.isoformat(),
            }
            for u in rows
        ],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.post("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise not_found("USER_NOT_FOUND", "User not found")
    target.is_active = False
    log_admin_action(db, admin, "USER_DEACTIVATED", "user", target.id)
    db.commit()
    return ok({"message": "User deactivated"})


@router.post("/users/{user_id}/activate")
def activate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise not_found("USER_NOT_FOUND", "User not found")
    target.is_active = True
    log_admin_action(db, admin, "USER_ACTIVATED", "user", target.id)
    db.commit()
    return ok({"message": "User activated"})
