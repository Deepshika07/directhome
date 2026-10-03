from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    Amenity,
    DocumentType,
    PropertyLocation,
    PropertyType,
    RoomConfiguration,
)
from app.schemas.common import ok
from app.schemas.misc import (
    DocumentTypeOut,
    FiltersOut,
    LocationSuggestion,
    LookupOut,
    PropertyTypeOut,
    RoomConfigurationOut,
)

router = APIRouter(tags=["meta"])


@router.get("/meta/filters")
def filter_options(db: Session = Depends(get_db)):
    """All dropdown/filter values for the UI in one call — types, BHK configs,
    amenities, furnishing, listing types, document types."""
    ptypes = db.scalars(
        select(PropertyType)
        .where(PropertyType.is_active.is_(True))
        .order_by(PropertyType.sort_order)
    ).all()
    rconfs = db.scalars(
        select(RoomConfiguration)
        .where(RoomConfiguration.is_active.is_(True))
        .order_by(RoomConfiguration.sort_order)
    ).all()
    amenities = db.scalars(
        select(Amenity)
        .where(Amenity.is_active.is_(True))
        .order_by(Amenity.sort_order)
    ).all()
    dtypes = db.scalars(
        select(DocumentType).where(DocumentType.is_active.is_(True))
    ).all()
    out = FiltersOut(
        listing_types=["RENT", "SALE", "PG", "SHARED"],
        furnishing_options=["UNFURNISHED", "SEMI_FURNISHED", "FULLY_FURNISHED"],
        property_types=[PropertyTypeOut.model_validate(t) for t in ptypes],
        room_configurations=[
            RoomConfigurationOut.model_validate(r) for r in rconfs
        ],
        amenities=[LookupOut.model_validate(a) for a in amenities],
        document_types=[DocumentTypeOut.model_validate(d) for d in dtypes],
    )
    return ok(out.model_dump(mode="json"))


@router.get("/locations/suggest")
def location_suggest(
    q: str = Query(min_length=2, description="city or locality fragment"),
    db: Session = Depends(get_db),
):
    """Autocomplete for the location filter — distinct cities and localities
    already used by listings."""
    pattern = f"{q.strip()}%"
    cities = db.scalars(
        select(PropertyLocation.city)
        .where(PropertyLocation.city.ilike(pattern))
        .distinct()
        .limit(8)
    ).all()
    localities = db.execute(
        select(PropertyLocation.locality, PropertyLocation.city)
        .where(PropertyLocation.locality.ilike(pattern))
        .distinct()
        .limit(12)
    ).all()
    suggestions = [
        LocationSuggestion(type="CITY", label=c, city=c) for c in cities
    ] + [
        LocationSuggestion(
            type="LOCALITY", label=f"{loc}, {city}", city=city, locality=loc
        )
        for loc, city in localities
    ]
    return ok([s.model_dump() for s in suggestions])
