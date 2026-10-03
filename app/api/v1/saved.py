from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Property, SavedProperty, User
from app.schemas.common import PageMeta, PageParams, ok
from app.services import property_service as svc

router = APIRouter(tags=["saved"])


@router.get("/users/me/saved-properties")
def list_saved(
    page_params: PageParams = Depends(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current user's saved listings (newest first)."""
    q = (
        select(Property)
        .join(SavedProperty, SavedProperty.property_id == Property.id)
        .where(SavedProperty.user_id == user.id)
        .order_by(SavedProperty.created_at.desc())
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
