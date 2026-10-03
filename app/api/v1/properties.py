from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    client_ip_hash,
    get_current_user,
    get_optional_user,
    require_seller,
)
from app.core.database import get_db
from app.core.errors import bad_request, conflict, not_found
from app.models import (
    PropertyAvailabilityHistory,
    PropertyInquiry,
    PropertyReport,
    SavedProperty,
    User,
)
from app.schemas.common import PageMeta, PageParams, ok
from app.schemas.misc import InquiryIn
from app.schemas.property import PropertyIn, PropertyUpdate, RejectIn, ReportIn
from app.services import property_service as svc

router = APIRouter(tags=["properties"])

LISTING_TYPES = {"RENT", "SALE", "PG", "SHARED"}
FURNISHING = {"UNFURNISHED", "SEMI_FURNISHED", "FULLY_FURNISHED"}


def _v(x):
    return x.value if hasattr(x, "value") else x


# ---------------- public browse ----------------

@router.get("/properties")
def search_properties(
    listing_type: str | None = Query(default=None),
    city: str | None = None,
    locality: str | None = None,
    pincode: str | None = None,
    category: str | None = Query(default=None, description="RESIDENTIAL|LAND|COMMERCIAL|PG"),
    property_types: str | None = Query(default=None, description="comma-separated codes, e.g. APARTMENT,VILLA"),
    room_configurations: str | None = Query(default=None, description="comma-separated codes, e.g. 2BHK,3BHK"),
    min_bedrooms: int | None = None,
    max_bedrooms: int | None = None,
    furnishing: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    min_area: int | None = None,
    max_area: int | None = None,
    available_from: date | None = None,
    owner_verified: bool | None = None,
    q: str | None = Query(default=None, description="title search"),
    sort: str = Query(default="newest", description="newest|price_asc|price_desc|trust_score"),
    page_params: PageParams = Depends(),
    db: Session = Depends(get_db),
):
    """Public search — only ACTIVE listings. All filters optional & composable."""
    filters = {
        "listing_type": listing_type.upper() if listing_type else None,
        "city": city, "locality": locality, "pincode": pincode,
        "category": category.upper() if category else None,
        "property_types": _csv(property_types),
        "room_configurations": _csv(room_configurations),
        "min_bedrooms": min_bedrooms, "max_bedrooms": max_bedrooms,
        "furnishing": furnishing.upper() if furnishing else None,
        "min_price": min_price, "max_price": max_price,
        "min_area": min_area, "max_area": max_area,
        "available_from": available_from,
        "owner_verified": owner_verified, "q": q, "sort": sort,
    }
    rows, total = svc.search(db, filters, page_params.page, page_params.limit)
    return ok(
        [svc.card_from(p).model_dump(mode="json") for p in rows],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


@router.get("/properties/{property_id}")
def property_detail(
    property_id: int,
    request: Request,
    x_session_id: str | None = None,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Public detail — records a view. Owner phone is NOT included."""
    prop = svc.get_property(db, property_id)
    if prop.status != "ACTIVE" and (user is None or user.id != prop.owner_id):
        raise not_found("PROPERTY_NOT_FOUND", "Property not found")
    svc.record_view(
        db, prop, user_id=user.id if user else None,
        session_id=x_session_id, ip_hash=client_ip_hash(request),
    )
    return ok(svc.detail_from(prop).model_dump(mode="json"))


# ---------------- seller listing management ----------------

@router.post("/properties", status_code=201)
def create_property(
    payload: PropertyIn,
    user: User = Depends(require_seller),
    db: Session = Depends(get_db),
):
    """Create a DRAFT listing (details + location + media + amenities in one call)."""
    prop = svc.create_property(db, user, payload)
    return ok(svc.detail_from(prop).model_dump(mode="json"))


@router.put("/properties/{property_id}")
def update_property(
    property_id: int,
    payload: PropertyUpdate,
    user: User = Depends(require_seller),
    db: Session = Depends(get_db),
):
    """Edit own listing. Editing an ACTIVE listing sends it back to PENDING_REVIEW."""
    prop = svc.get_owned_property(db, property_id, user)
    prop = svc.update_property(db, prop, payload)
    return ok(svc.detail_from(prop).model_dump(mode="json"))


@router.delete("/properties/{property_id}")
def delete_property(
    property_id: int,
    user: User = Depends(require_seller),
    db: Session = Depends(get_db),
):
    """Delete a listing — allowed only while DRAFT or REJECTED."""
    prop = svc.get_owned_property(db, property_id, user)
    if prop.status not in {"DRAFT", "REJECTED"}:
        raise bad_request(
            "CANNOT_DELETE",
            "Only DRAFT or REJECTED listings can be deleted",
        )
    db.delete(prop)
    db.commit()
    return ok({"message": "Listing deleted"})


@router.get("/users/me/properties")
def my_properties(
    page_params: PageParams = Depends(),
    user: User = Depends(require_seller),
    db: Session = Depends(get_db),
):
    """Seller's own listings in every status (dashboard)."""
    from sqlalchemy import func, select
    from app.models import Property

    q = select(Property).where(Property.owner_id == user.id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(Property.created_at.desc())
        .offset(page_params.offset)
        .limit(page_params.limit)
        .options(*svc._eager())
    ).all()
    return ok(
        [svc.card_from(p).model_dump(mode="json") for p in rows],
        PageMeta(page=page_params.page, limit=page_params.limit, total=total),
    )


# ---------------- status transitions ----------------

def _transition(property_id: int, action: str, user: User, db: Session,
                reason: str | None = None):
    prop = svc.get_owned_property(db, property_id, user)
    prop = svc.transition(db, prop, action, user, reason)
    return ok(svc.detail_from(prop).model_dump(mode="json"))


@router.post("/properties/{property_id}/submit")
def submit(property_id: int, user: User = Depends(require_seller),
           db: Session = Depends(get_db)):
    """DRAFT/REJECTED → PENDING_REVIEW (validates listing completeness)."""
    return _transition(property_id, "submit", user, db)


@router.post("/properties/{property_id}/pause")
def pause(property_id: int, user: User = Depends(require_seller),
          db: Session = Depends(get_db)):
    """ACTIVE → PAUSED."""
    return _transition(property_id, "pause", user, db)


@router.post("/properties/{property_id}/activate")
def activate(property_id: int, user: User = Depends(require_seller),
             db: Session = Depends(get_db)):
    """PAUSED → ACTIVE."""
    return _transition(property_id, "activate", user, db)


@router.post("/properties/{property_id}/mark-rented")
def mark_rented(property_id: int, user: User = Depends(require_seller),
                db: Session = Depends(get_db)):
    """ACTIVE/PAUSED → RENTED."""
    return _transition(property_id, "mark_rented", user, db)


@router.post("/properties/{property_id}/mark-sold")
def mark_sold(property_id: int, user: User = Depends(require_seller),
              db: Session = Depends(get_db)):
    """ACTIVE/PAUSED → SOLD."""
    return _transition(property_id, "mark_sold", user, db)


@router.post("/properties/{property_id}/availability")
def confirm_availability(
    property_id: int,
    available: bool = True,
    user: User = Depends(require_seller),
    db: Session = Depends(get_db),
):
    """Seller confirms the listing is still available — feeds the freshness label."""
    prop = svc.get_owned_property(db, property_id, user)
    now = datetime.now(timezone.utc)
    prop.availability_status = "AVAILABLE" if available else "EXPIRED"
    prop.availability_confirmed_at = now
    db.add(
        PropertyAvailabilityHistory(
            property_id=prop.id,
            status="AVAILABLE" if available else "UNAVAILABLE",
            confirmed_by=user.id,
            confirmation_method="OWNER_APP",
            confirmed_at=now,
        )
    )
    db.commit()
    return ok({"message": "Availability updated", "availability_status": prop.availability_status})


# ---------------- buyer actions ----------------

@router.post("/properties/{property_id}/save", status_code=201)
def save_property(property_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Save a listing to favorites."""
    svc.get_property(db, property_id)
    exists = db.scalar(
        select(SavedProperty.id).where(
            SavedProperty.user_id == user.id,
            SavedProperty.property_id == property_id,
        )
    )
    if exists:
        raise conflict("ALREADY_SAVED", "Property already saved")
    db.add(SavedProperty(user_id=user.id, property_id=property_id))
    db.commit()
    return ok({"message": "Property saved"})


@router.delete("/properties/{property_id}/save")
def unsave_property(property_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Remove a listing from favorites."""
    saved = db.scalar(
        select(SavedProperty).where(
            SavedProperty.user_id == user.id,
            SavedProperty.property_id == property_id,
        )
    )
    if saved is None:
        raise not_found("NOT_SAVED", "Property was not saved")
    db.delete(saved)
    db.commit()
    return ok({"message": "Property unsaved"})


@router.post("/properties/{property_id}/inquiries", status_code=201)
def create_inquiry(
    property_id: int,
    payload: InquiryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Contact owner — records a lead and reveals the seller's phone number."""
    prop = svc.get_property(db, property_id)
    if prop.status != "ACTIVE":
        raise not_found("PROPERTY_NOT_FOUND", "Property not found")
    if prop.owner_id == user.id:
        raise bad_request("OWN_LISTING", "You cannot inquire about your own listing")
    if payload.contact_method not in {"PHONE", "WHATSAPP", "EMAIL", "PLATFORM"}:
        raise bad_request("INVALID_CONTACT_METHOD", "Unknown contact_method")
    inquiry = PropertyInquiry(
        property_id=prop.id, buyer_id=user.id, owner_id=prop.owner_id,
        message=payload.message, contact_method=payload.contact_method,
    )
    db.add(inquiry)
    db.commit()
    owner = prop.owner
    return ok(
        {
            "inquiry": {
                "id": inquiry.id, "property_id": prop.id,
                "buyer_id": user.id, "owner_id": prop.owner_id,
                "message": inquiry.message,
                "contact_method": _v(inquiry.contact_method),
                "status": _v(inquiry.status),
                "created_at": inquiry.created_at.isoformat(),
            },
            "owner_contact": {"name": owner.name, "phone": owner.phone},
        }
    )


@router.post("/properties/{property_id}/reports", status_code=201)
def report_property(
    property_id: int,
    payload: ReportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Flag a listing (fake, broker, scam, etc.) for admin review."""
    svc.get_property(db, property_id)
    reasons = {"FAKE_LISTING", "ALREADY_RENTED", "ALREADY_SOLD",
               "WRONG_INFORMATION", "BROKER", "DUPLICATE", "SCAM",
               "INAPPROPRIATE", "OTHER"}
    if payload.reason not in reasons:
        raise bad_request("INVALID_REASON", "Unknown report reason")
    db.add(
        PropertyReport(
            property_id=property_id, reported_by=user.id,
            reason=payload.reason, description=payload.description,
        )
    )
    db.commit()
    return ok({"message": "Report submitted"})


def _csv(v: str | None) -> list[str] | None:
    if not v:
        return None
    return [x.strip().upper() for x in v.split(",") if x.strip()]
