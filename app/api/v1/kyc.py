from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import not_found
from app.models import (
    KycDocumentType,
    KycRejectionReason,
    SellerClaimType,
    User,
)
from app.schemas.common import ok
from app.schemas.kyc import (
    ClaimIn,
    ConsentIn,
    DocInitIn,
    KycMetaOut,
    PanIn,
)
from app.services import kyc_service
from app.services.providers import get_provider

router = APIRouter(tags=["kyc"])


@router.post("/kyc/consent")
def give_consent(payload: ConsentIn, request: Request,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    rec = kyc_service.record_consent(
        db, user, payload.purpose,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    return ok({"purpose": rec.purpose, "notice_version": rec.notice_version,
               "accepted_at": rec.accepted_at})


@router.get("/kyc/me")
def my_kyc(user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    return ok(kyc_service.profile_out(db, user))


@router.get("/kyc/meta", response_model=None)
def kyc_meta(db: Session = Depends(get_db)):
    claim_types = db.scalars(select(SellerClaimType).where(
        SellerClaimType.is_active.is_(True)).order_by(
        SellerClaimType.sort_order)).all()
    doc_types = db.scalars(select(KycDocumentType).where(
        KycDocumentType.is_active.is_(True)).order_by(
        KycDocumentType.sort_order)).all()
    reasons = db.scalars(select(KycRejectionReason).where(
        KycRejectionReason.is_active.is_(True)).order_by(
        KycRejectionReason.sort_order)).all()
    return ok(KycMetaOut(
        claim_types=[ClaimTypeOutAdapter(c) for c in claim_types],
        document_types=[DocTypeAdapter(d) for d in doc_types],
        rejection_reasons=[ReasonAdapter(r) for r in reasons],
        notice_version=settings.kyc_notice_version,
        grievance_email=settings.kyc_grievance_email,
    ))


def ClaimTypeOutAdapter(c):
    return {"id": c.id, "code": c.code, "name": c.name,
            "description": c.description,
            "required_doc_codes": c.required_doc_codes,
            "requires_admin_review": c.requires_admin_review}


def DocTypeAdapter(d):
    return {"id": d.id, "code": d.code, "name": d.name,
            "applies_to": d.applies_to.value if d.applies_to else None,
            "claim_type_codes": d.claim_type_codes}


def ReasonAdapter(r):
    return {"id": r.id, "code": r.code, "name": r.name,
            "applies_to": r.applies_to.value if r.applies_to else None}


@router.post("/kyc/identity/pan")
def submit_pan(payload: PanIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    profile = kyc_service.submit_pan(
        db, user, payload.pan, payload.name_as_per_pan)
    return ok(kyc_service.profile_out(db, user))


@router.post("/kyc/identity/digilocker/initiate")
def digilocker_initiate(user: User = Depends(get_current_user)):
    res = get_provider().digilocker_initiate(user.id, "")
    return ok({"supported": res.ok,
               "message": "DigiLocker is not enabled yet — upload documents "
                          "manually for now."})


@router.get("/kyc/identity/digilocker/callback")
def digilocker_callback(state: str = "", code: str = ""):
    res = get_provider().digilocker_callback(state, code)
    return ok({"supported": res.ok,
               "message": "DigiLocker is not enabled yet."})


@router.post("/kyc/documents")
def init_document(payload: DocInitIn,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    doc = kyc_service.init_document(
        db, user, payload.doc_type_id, payload.filename,
        payload.mime, payload.size_bytes)
    return ok({
        "document_id": doc.id,
        "upload_url": f"/api/v1/kyc/documents/{doc.id}/content",
        "max_bytes": settings.kyc_max_doc_mb * 1024 * 1024,
    })


@router.post("/kyc/documents/{doc_id}/content")
async def upload_content(doc_id: int, request: Request,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    data = await request.body()
    doc = kyc_service.upload_document_content(
        db, user, doc_id, data, request.headers.get("content-type"))
    status = doc.status.value if hasattr(doc.status, "value") else doc.status
    return ok({"id": doc.id, "status": status,
               "sha256": doc.sha256, "uploaded_at": doc.uploaded_at})


@router.post("/kyc/submit")
def submit_kyc(user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    kyc_service.submit_kyc(db, user)
    return ok(kyc_service.profile_out(db, user))


@router.post("/properties/{property_id}/ownership-claim")
def create_claim(property_id: int, payload: ClaimIn,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    from app.services import property_service

    prop = property_service.get_property(db, property_id)
    claim = kyc_service.create_claim(
        db, user, prop, payload.claim_type_id, payload.owner_name_on_doc)
    return ok(_claim_out(db, claim))


@router.post("/properties/{property_id}/ownership-claim/documents")
def init_claim_document(property_id: int, payload: DocInitIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    doc = kyc_service.init_document(
        db, user, payload.doc_type_id, payload.filename,
        payload.mime, payload.size_bytes, property_id=property_id)
    return ok({
        "document_id": doc.id,
        "upload_url": f"/api/v1/kyc/documents/{doc.id}/content",
        "max_bytes": settings.kyc_max_doc_mb * 1024 * 1024,
    })


@router.get("/properties/{property_id}/ownership-claim")
def get_claim(property_id: int, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    claim = kyc_service.get_claim(db, property_id)
    if claim is None:
        raise not_found("CLAIM_NOT_FOUND", "No ownership claim for this listing")
    if claim.property.owner_id != user.id:
        from app.api.deps import user_role_codes
        roles = user_role_codes(db, user.id)
        if not set(roles) & {"ADMIN", "KYC_REVIEWER"}:
            from app.core.errors import forbidden
            raise forbidden("Not your listing")
    return ok(_claim_out(db, claim))


def _claim_out(db: Session, claim) -> dict:
    docs = kyc_service.list_documents(db, claim.property.owner_id,
                                      property_id=claim.property_id)
    return {
        "id": claim.id,
        "property_id": claim.property_id,
        "status": claim.status.value if claim.status else None,
        "claim_type": claim.claim_type.code if claim.claim_type else None,
        "owner_name_on_doc": claim.owner_name_on_doc,
        "name_match_score": claim.name_match_score,
        "created_at": claim.created_at,
        "documents": [{"id": d.id, "doc_type": d.doc_type.code,
                       "status": d.status.value, "mime": d.mime,
                       "size_bytes": d.size_bytes,
                       "uploaded_at": d.uploaded_at} for d in docs],
    }
