from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.errors import bad_request
from app.models import DocumentType, User, VerificationDocument, enums
from app.schemas.common import ok
from app.schemas.misc import DocumentIn, DocumentOut

router = APIRouter(tags=["documents"])


@router.post("/users/me/documents", status_code=201)
def upload_document(
    payload: DocumentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a KYC/property document (file URL) for admin verification."""
    doc_type = db.get(DocumentType, payload.document_type_id)
    if doc_type is None or not doc_type.is_active:
        raise bad_request("INVALID_DOC_TYPE", "Unknown document_type_id")
    doc = VerificationDocument(
        user_id=user.id,
        property_id=payload.property_id,
        document_type_id=payload.document_type_id,
        file_url=payload.file_url,
    )
    db.add(doc)
    if user.kyc_status in {"NOT_SUBMITTED", "REJECTED"}:
        user.kyc_status = enums.KycStatus.PENDING
    db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


@router.get("/users/me/documents")
def my_documents(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current user's submitted documents and their verification status."""
    rows = db.scalars(
        select(VerificationDocument)
        .where(VerificationDocument.user_id == user.id)
        .order_by(VerificationDocument.created_at.desc())
    ).all()
    return ok(
        [DocumentOut.model_validate(d).model_dump(mode="json") for d in rows]
    )
