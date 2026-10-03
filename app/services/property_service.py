from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import bad_request, forbidden, not_found
from app.models import (
    Amenity,
    Property,
    PropertyAmenity,
    PropertyLocation,
    PropertyMedia,
    PropertyStatusHistory,
    PropertyType,
    RoomConfiguration,
    User,
    enums,
)
from app.schemas.property import (
    LocationIn,
    LocationOut,
    MediaOut,
    OwnerBrief,
    PropertyCard,
    PropertyDetail,
    PropertyIn,
    PropertyUpdate,
)

EDITABLE_STATUSES = {"DRAFT", "REJECTED", "PAUSED", "ACTIVE"}

TRANSITIONS = {
    "submit": ({"DRAFT", "REJECTED"}, "PENDING_REVIEW"),
    "approve": ({"PENDING_REVIEW"}, "ACTIVE"),
    "reject": ({"PENDING_REVIEW"}, "REJECTED"),
    "pause": ({"ACTIVE"}, "PAUSED"),
    "activate": ({"PAUSED"}, "ACTIVE"),
    "mark_rented": ({"ACTIVE", "PAUSED"}, "RENTED"),
    "mark_sold": ({"ACTIVE", "PAUSED"}, "SOLD"),
}


def _eager():
    return [
        selectinload(Property.location),
        selectinload(Property.media),
        selectinload(Property.amenities).selectinload(PropertyAmenity.amenity),
        selectinload(Property.property_type),
        selectinload(Property.room_configuration),
        selectinload(Property.owner),
    ]


def get_property(db: Session, property_id: int) -> Property:
    prop = db.scalar(
        select(Property).where(Property.id == property_id).options(*_eager())
    )
    if prop is None:
        raise not_found("PROPERTY_NOT_FOUND", "Property not found")
    return prop


def get_owned_property(db: Session, property_id: int, user: User) -> Property:
    prop = get_property(db, property_id)
    if prop.owner_id != user.id:
        raise forbidden("You can only manage your own listings")
    return prop


def _validate_refs(db: Session, property_type_id, room_configuration_id, amenity_ids):
    ptype = db.get(PropertyType, property_type_id)
    if ptype is None or not ptype.is_active:
        raise bad_request("INVALID_PROPERTY_TYPE", "Unknown property_type_id")
    if room_configuration_id is not None:
        rc = db.get(RoomConfiguration, room_configuration_id)
        if rc is None or not rc.is_active:
            raise bad_request(
                "INVALID_ROOM_CONFIG", "Unknown room_configuration_id"
            )
    if amenity_ids:
        found = db.scalars(
            select(func.count(Amenity.id)).where(Amenity.id.in_(amenity_ids))
        ).one()
        if found != len(set(amenity_ids)):
            raise bad_request("INVALID_AMENITY", "One or more amenity_ids are invalid")


def _apply_location(prop: Property, loc: LocationIn) -> None:
    prop.location = PropertyLocation(**loc.model_dump())


def _apply_media(prop: Property, media) -> None:
    prop.media.clear()
    primary_seen = False
    for m in media:
        item = PropertyMedia(**m.model_dump())
        if item.is_primary:
            if primary_seen:
                item.is_primary = False
            primary_seen = True
        prop.media.append(item)


def _apply_amenities(prop: Property, amenity_ids: list[int]) -> None:
    prop.amenities.clear()
    for aid in dict.fromkeys(amenity_ids):
        prop.amenities.append(PropertyAmenity(amenity_id=aid))


SCALAR_FIELDS = [
    "title", "description", "listing_type", "property_type_id",
    "room_configuration_id", "bathrooms", "carpet_area_sqft",
    "built_up_area_sqft", "plot_area_sqft", "floor_number", "total_floors",
    "furnishing", "rent_amount", "security_deposit", "maintenance_amount",
    "sale_price", "price_negotiable", "available_from", "extra_attributes",
]


def create_property(db: Session, owner: User, payload: PropertyIn) -> Property:
    _validate_refs(db, payload.property_type_id, payload.room_configuration_id,
                   payload.amenity_ids)
    prop = Property(owner_id=owner.id)
    for f in SCALAR_FIELDS:
        setattr(prop, f, getattr(payload, f))
    if payload.location:
        _apply_location(prop, payload.location)
    if payload.media:
        _apply_media(prop, payload.media)
    if payload.amenity_ids:
        _apply_amenities(prop, payload.amenity_ids)
    db.add(prop)
    db.flush()
    _record_status(db, prop, None, "DRAFT", owner.id)
    db.commit()
    return get_property(db, prop.id)


def update_property(db: Session, prop: Property, payload: PropertyUpdate) -> Property:
    if prop.status not in EDITABLE_STATUSES:
        raise bad_request(
            "NOT_EDITABLE",
            f"Listing in status {prop.status} cannot be edited",
        )
    data = payload.model_dump(exclude_unset=True)
    _validate_refs(
        db,
        data.get("property_type_id", prop.property_type_id),
        data.get("room_configuration_id", prop.room_configuration_id),
        data.get("amenity_ids") or [],
    )
    for f in SCALAR_FIELDS:
        if f in data:
            setattr(prop, f, data[f])
    if "location" in data and payload.location is not None:
        _apply_location(prop, payload.location)
    if "media" in data and payload.media is not None:
        _apply_media(prop, payload.media)
    if "amenity_ids" in data and payload.amenity_ids is not None:
        _apply_amenities(prop, payload.amenity_ids)
    # edits on a live listing send it back for review
    if prop.status == "ACTIVE":
        _record_status(db, prop, "ACTIVE", "PENDING_REVIEW", prop.owner_id,
                       "Edited after publish")
        prop.status = "PENDING_REVIEW"
    db.commit()
    return get_property(db, prop.id)


def _check_publishable(prop: Property) -> None:
    problems = []
    if not prop.description:
        problems.append("description")
    if prop.location is None:
        problems.append("location")
    if not any(m.media_type == "IMAGE" for m in prop.media):
        problems.append("at least one image")
    if prop.listing_type == "SALE" and prop.sale_price is None:
        problems.append("sale_price")
    if prop.listing_type != "SALE" and prop.rent_amount is None:
        problems.append("rent_amount")
    if (
        prop.property_type
        and prop.property_type.category == "RESIDENTIAL"
        and prop.room_configuration_id is None
    ):
        problems.append("room_configuration_id")
    if problems:
        raise bad_request(
            "LISTING_INCOMPLETE",
            "Missing required fields for publishing: " + ", ".join(problems),
        )


def _record_status(db, prop, old, new, changed_by, reason=None):
    db.add(
        PropertyStatusHistory(
            property_id=prop.id, old_status=old, new_status=new,
            changed_by=changed_by, reason=reason,
        )
    )


def transition(db: Session, prop: Property, action: str, actor: User,
               reason: str | None = None) -> Property:
    if action not in TRANSITIONS:
        raise bad_request("INVALID_ACTION", f"Unknown action {action}")
    allowed_from, new_status = TRANSITIONS[action]
    if prop.status not in allowed_from:
        raise bad_request(
            "INVALID_STATUS_TRANSITION",
            f"Cannot {action} a listing in status {prop.status}",
        )
    if new_status == "PENDING_REVIEW":
        _check_publishable(prop)
    _record_status(db, prop, prop.status, new_status, actor.id, reason)
    prop.status = new_status
    db.commit()
    return get_property(db, prop.id)


def record_view(db: Session, prop: Property, user_id=None, session_id=None,
                ip_hash=None) -> None:
    from app.models import PropertyView

    db.add(
        PropertyView(
            property_id=prop.id, user_id=user_id,
            session_id=session_id, ip_hash=ip_hash,
        )
    )
    db.commit()


# ---------------- search ----------------

def search(db: Session, f: dict, page: int, limit: int):
    price = func.coalesce(Property.rent_amount, Property.sale_price)
    area = func.coalesce(Property.carpet_area_sqft, Property.plot_area_sqft)

    q = (
        select(Property)
        .join(PropertyLocation, PropertyLocation.property_id == Property.id)
        .join(PropertyType, PropertyType.id == Property.property_type_id)
        .where(Property.status == "ACTIVE")
        .options(*_eager())
    )

    if f.get("listing_type"):
        q = q.where(Property.listing_type == f["listing_type"])
    if f.get("city"):
        q = q.where(PropertyLocation.city.ilike(f["city"]))
    if f.get("locality"):
        q = q.where(PropertyLocation.locality.ilike(f["locality"]))
    if f.get("pincode"):
        q = q.where(PropertyLocation.pincode == f["pincode"])
    if f.get("category"):
        q = q.where(PropertyType.category == f["category"])
    if f.get("property_types"):
        q = q.where(PropertyType.code.in_(f["property_types"]))
    if f.get("room_configurations"):
        q = q.join(
            RoomConfiguration,
            RoomConfiguration.id == Property.room_configuration_id,
        ).where(RoomConfiguration.code.in_(f["room_configurations"]))
    if f.get("min_bedrooms") is not None or f.get("max_bedrooms") is not None:
        q = q.join(
            RoomConfiguration,
            RoomConfiguration.id == Property.room_configuration_id,
        )
        if f.get("min_bedrooms") is not None:
            q = q.where(RoomConfiguration.bedrooms >= f["min_bedrooms"])
        if f.get("max_bedrooms") is not None:
            q = q.where(RoomConfiguration.bedrooms <= f["max_bedrooms"])
    if f.get("furnishing"):
        q = q.where(Property.furnishing == f["furnishing"])
    if f.get("min_price") is not None:
        q = q.where(price >= f["min_price"])
    if f.get("max_price") is not None:
        q = q.where(price <= f["max_price"])
    if f.get("min_area") is not None:
        q = q.where(area >= f["min_area"])
    if f.get("max_area") is not None:
        q = q.where(area <= f["max_area"])
    if f.get("available_from"):
        q = q.where(
            or_(Property.available_from.is_(None),
                Property.available_from <= f["available_from"])
        )
    if f.get("owner_verified"):
        q = q.where(Property.is_owner_verified.is_(True))
    if f.get("q"):
        q = q.where(Property.title.ilike(f"%{f['q']}%"))

    sort = f.get("sort") or "newest"
    if sort == "price_asc":
        q = q.order_by(price.asc())
    elif sort == "price_desc":
        q = q.order_by(price.desc())
    elif sort == "trust_score":
        q = q.order_by(Property.trust_score.desc(), Property.created_at.desc())
    else:
        q = q.order_by(Property.created_at.desc())

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.offset((page - 1) * limit).limit(limit)).all()
    return rows, total


# ---------------- serializers ----------------

def _primary_image(prop: Property) -> str | None:
    images = [m for m in prop.media if m.media_type == "IMAGE"]
    if not images:
        return None
    primary = next((m for m in images if m.is_primary), images[0])
    return primary.url


def _area(prop: Property) -> int | None:
    return prop.carpet_area_sqft or prop.plot_area_sqft or prop.built_up_area_sqft


def _v(x):
    return x.value if hasattr(x, "value") else x


def card_from(prop: Property) -> PropertyCard:
    loc = prop.location
    return PropertyCard(
        id=prop.id,
        title=prop.title,
        listing_type=_v(prop.listing_type),
        property_type=prop.property_type.name if prop.property_type else "",
        room_configuration=(
            prop.room_configuration.name if prop.room_configuration else None
        ),
        bedrooms=(
            prop.room_configuration.bedrooms if prop.room_configuration else None
        ),
        bathrooms=prop.bathrooms,
        furnishing=_v(prop.furnishing) if prop.furnishing else None,
        rent_amount=prop.rent_amount,
        sale_price=prop.sale_price,
        area_sqft=_area(prop),
        locality=loc.locality if loc else None,
        city=loc.city if loc else None,
        primary_image=_primary_image(prop),
        trust_score=prop.trust_score or 0,
        is_owner_verified=prop.is_owner_verified,
        status=_v(prop.status),
        available_from=prop.available_from,
        created_at=prop.created_at,
    )


def detail_from(prop: Property) -> PropertyDetail:
    card = card_from(prop).model_dump()
    owner = prop.owner
    return PropertyDetail(
        **card,
        description=prop.description,
        security_deposit=prop.security_deposit,
        maintenance_amount=prop.maintenance_amount,
        price_negotiable=prop.price_negotiable,
        carpet_area_sqft=prop.carpet_area_sqft,
        built_up_area_sqft=prop.built_up_area_sqft,
        plot_area_sqft=prop.plot_area_sqft,
        floor_number=prop.floor_number,
        total_floors=prop.total_floors,
        availability_status=_v(prop.availability_status),
        last_verified_at=prop.last_verified_at,
        extra_attributes=prop.extra_attributes or {},
        location=LocationOut.model_validate(prop.location) if prop.location else None,
        media=[MediaOut.model_validate(m) for m in prop.media],
        amenities=sorted(a.amenity.name for a in prop.amenities),
        owner=(
            OwnerBrief(
                id=owner.id, name=owner.name,
                is_verified=owner.kyc_status == "VERIFIED",
            )
            if owner
            else None
        ),
        updated_at=prop.updated_at,
    )
