from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.errors import bad_request, forbidden, not_found
from app.models import PropertyInquiry, User
from app.schemas.common import PageMeta, PageParams, ok

router = APIRouter(tags=["inquiries"])


class InquiryStatusIn(BaseModel):
    status: str  # CONTACTED | RESPONDED | CLOSED


def _v(x):
    return x.value if hasattr(x, "value") else x


def _row(i: PropertyInquiry) -> dict:
    return {
        "id": i.id, "property_id": i.property_id, "buyer_id": i.buyer_id,
        "owner_id": i.owner_id, "message": i.message,
        "contact_method": _v(i.contact_method), "status": _v(i.status),
        "created_at": i.created_at.isoformat(),
    }


@router.get("/users/me/inquiries")
def my_inquiries(
    page_params: PageParams = Depends(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Inquiries the current user has sent (buyer view)."""
    q = (
        select(PropertyInquiry)
        .where(PropertyInquiry.buyer_id == user.id)
        .order_by(PropertyInquiry.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [_row(i) for i in rows],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.get("/owner/inquiries")
def owner_inquiries(
    page_params: PageParams = Depends(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leads received on the current user's listings (seller view)."""
    q = (
        select(PropertyInquiry)
        .where(PropertyInquiry.owner_id == user.id)
        .order_by(PropertyInquiry.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.offset(page_params.offset).limit(page_params.limit)
    ).all()
    return ok(
        [_row(i) for i in rows],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.patch("/inquiries/{inquiry_id}")
def update_inquiry_status(
    inquiry_id: int,
    payload: InquiryStatusIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Seller updates a lead's status: CONTACTED | RESPONDED | CLOSED."""
    inquiry = db.get(PropertyInquiry, inquiry_id)
    if inquiry is None:
        raise not_found("INQUIRY_NOT_FOUND", "Inquiry not found")
    if inquiry.owner_id != user.id:
        raise forbidden("Only the listing owner can update an inquiry")
    if payload.status not in {"CONTACTED", "RESPONDED", "CLOSED"}:
        raise bad_request("INVALID_STATUS", "Status must be CONTACTED, RESPONDED or CLOSED")
    inquiry.status = payload.status
    db.commit()
    return ok(_row(inquiry))
