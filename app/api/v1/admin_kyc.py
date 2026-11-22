from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import require_kyc_reviewer
from app.core.database import get_db
from app.core.errors import bad_request, not_found
from app.models import KycDocument, User
from app.schemas.common import PageMeta, PageParams, ok
from app.schemas.kyc import ActionIn, CaseOut, DecisionIn, QueueItem
from app.services import kyc_service
from app.services.audit_service import log_admin_action
from app.services.storage import get_storage

router = APIRouter(prefix="/admin/kyc", tags=["admin-kyc"])

SUBJECT_TYPES = {"KYC_PROFILE", "OWNERSHIP_CLAIM"}


def _subject_type(raw: str) -> str:
    st = raw.upper()
    if st not in SUBJECT_TYPES:
        raise bad_request("BAD_SUBJECT_TYPE",
                          "subject_type must be KYC_PROFILE or OWNERSHIP_CLAIM")
    return st


@router.get("/queue")
def queue(status: str | None = None, claim_type: str | None = None,
          min_risk: int | None = None, sla_breached: bool | None = None,
          page: PageParams = Depends(),
          reviewer: User = Depends(require_kyc_reviewer),
          db: Session = Depends(get_db)):
    items, total = kyc_service.review_queue(
        db, status, claim_type, min_risk, sla_breached,
        page.page, page.limit)
    return ok([QueueItem(**i) for i in items],
              PageMeta(page=page.page, limit=page.limit, total=total))


@router.get("/cases/{subject_type}/{subject_id}", response_model=None)
def case_view(subject_type: str, subject_id: int,
              reviewer: User = Depends(require_kyc_reviewer),
              db: Session = Depends(get_db)):
    return ok(CaseOut(**kyc_service.case_detail(
        db, _subject_type(subject_type), subject_id)))


@router.post("/cases/{subject_type}/{subject_id}/decision")
def decide(subject_type: str, subject_id: int, payload: DecisionIn,
           reviewer: User = Depends(require_kyc_reviewer),
           db: Session = Depends(get_db)):
    result = kyc_service.decide(
        db, reviewer, _subject_type(subject_type), subject_id,
        payload.decision, payload.reason_id, payload.notes)
    return ok(result)


@router.post("/cases/{subject_type}/{subject_id}/request-info")
def request_info(subject_type: str, subject_id: int, payload: ActionIn,
                 reviewer: User = Depends(require_kyc_reviewer),
                 db: Session = Depends(get_db)):
    return ok(kyc_service.request_info(
        db, reviewer, _subject_type(subject_type), subject_id,
        payload.reason_id, payload.notes))


@router.post("/cases/{subject_type}/{subject_id}/escalate")
def escalate(subject_type: str, subject_id: int, payload: ActionIn,
             reviewer: User = Depends(require_kyc_reviewer),
             db: Session = Depends(get_db)):
    return ok(kyc_service.escalate(
        db, reviewer, _subject_type(subject_type), subject_id,
        payload.notes))


@router.get("/documents/{doc_id}/url")
def document_url(doc_id: int,
                 reviewer: User = Depends(require_kyc_reviewer),
                 db: Session = Depends(get_db)):
    doc = db.get(KycDocument, doc_id)
    if doc is None:
        raise not_found("DOC_NOT_FOUND", "Document not found")
    # every staff document access is logged before the URL is handed out
    log_admin_action(db, reviewer, "KYC_DOC_ACCESS", "kyc_document",
                     doc_id, extra={"owner_user_id": doc.owner_user_id,
                                    "property_id": doc.property_id})
    db.commit()
    return ok({"url": f"/api/v1/admin/kyc/documents/{doc.id}/content",
               "expires_in": 300,
               "note": "single-purpose logged access; fetch promptly"})


@router.get("/documents/{doc_id}/content")
def document_content(doc_id: int,
                     reviewer: User = Depends(require_kyc_reviewer),
                     db: Session = Depends(get_db)):
    doc = db.get(KycDocument, doc_id)
    if doc is None:
        raise not_found("DOC_NOT_FOUND", "Document not found")
    log_admin_action(db, reviewer, "KYC_DOC_VIEW", "kyc_document",
                     doc_id, extra={"owner_user_id": doc.owner_user_id,
                                    "property_id": doc.property_id})
    db.commit()
    data = get_storage().get(doc.storage_key)
    return Response(content=data, media_type=doc.mime)
