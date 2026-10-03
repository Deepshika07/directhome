from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocationIn(BaseModel):
    address_line1: str | None = None
    address_line2: str | None = None
    locality: str
    city: str
    state: str
    pincode: str | None = None
    landmark: str | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)

    @field_validator("pincode")
    @classmethod
    def _pincode(cls, v: str | None) -> str | None:
        if v is not None and not (v.isdigit() and len(v) == 6):
            raise ValueError("pincode must be 6 digits")
        return v


class LocationOut(LocationIn):
    model_config = ConfigDict(from_attributes=True)
    country: str = "India"


class MediaIn(BaseModel):
    media_type: str = "IMAGE"  # IMAGE | VIDEO | FLOOR_PLAN | VIRTUAL_TOUR
    url: str
    thumbnail_url: str | None = None
    display_order: int = 0
    is_primary: bool = False


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: str
    url: str
    thumbnail_url: str | None
    display_order: int
    is_primary: bool


class PropertyIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str | None = None
    listing_type: str  # RENT | SALE | PG | SHARED
    property_type_id: int
    room_configuration_id: int | None = None
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    carpet_area_sqft: int | None = Field(default=None, ge=0)
    built_up_area_sqft: int | None = Field(default=None, ge=0)
    plot_area_sqft: int | None = Field(default=None, ge=0)
    floor_number: int | None = Field(default=None, ge=-5, le=200)
    total_floors: int | None = Field(default=None, ge=0, le=200)
    furnishing: str | None = None  # UNFURNISHED | SEMI_FURNISHED | FULLY_FURNISHED
    rent_amount: Decimal | None = Field(default=None, ge=0)
    security_deposit: Decimal | None = Field(default=None, ge=0)
    maintenance_amount: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)
    price_negotiable: bool = False
    available_from: date | None = None
    extra_attributes: dict[str, Any] = {}
    location: LocationIn | None = None
    media: list[MediaIn] = []
    amenity_ids: list[int] = []


class PropertyUpdate(BaseModel):
    """All fields optional; provided fields replace existing values."""

    title: str | None = Field(default=None, min_length=5, max_length=200)
    description: str | None = None
    listing_type: str | None = None
    property_type_id: int | None = None
    room_configuration_id: int | None = None
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    carpet_area_sqft: int | None = Field(default=None, ge=0)
    built_up_area_sqft: int | None = Field(default=None, ge=0)
    plot_area_sqft: int | None = Field(default=None, ge=0)
    floor_number: int | None = Field(default=None, ge=-5, le=200)
    total_floors: int | None = Field(default=None, ge=0, le=200)
    furnishing: str | None = None
    rent_amount: Decimal | None = Field(default=None, ge=0)
    security_deposit: Decimal | None = Field(default=None, ge=0)
    maintenance_amount: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)
    price_negotiable: bool | None = None
    available_from: date | None = None
    extra_attributes: dict[str, Any] | None = None
    location: LocationIn | None = None
    media: list[MediaIn] | None = None
    amenity_ids: list[int] | None = None


class OwnerBrief(BaseModel):
    id: int
    name: str
    is_verified: bool  # kyc_status == VERIFIED
    # phone intentionally omitted — revealed only via inquiry endpoint


class PropertyCard(BaseModel):
    """Lighter shape for search results."""

    id: int
    title: str
    listing_type: str
    property_type: str
    room_configuration: str | None
    bedrooms: int | None
    bathrooms: int | None
    furnishing: str | None
    rent_amount: Decimal | None
    sale_price: Decimal | None
    area_sqft: int | None
    locality: str | None
    city: str | None
    primary_image: str | None
    trust_score: int
    is_owner_verified: bool
    status: str
    available_from: date | None
    created_at: datetime


class PropertyDetail(PropertyCard):
    description: str | None
    security_deposit: Decimal | None
    maintenance_amount: Decimal | None
    price_negotiable: bool
    carpet_area_sqft: int | None
    built_up_area_sqft: int | None
    plot_area_sqft: int | None
    floor_number: int | None
    total_floors: int | None
    availability_status: str
    last_verified_at: datetime | None
    extra_attributes: dict
    location: LocationOut | None
    media: list[MediaOut]
    amenities: list[str]
    owner: OwnerBrief | None
    updated_at: datetime


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ReportIn(BaseModel):
    reason: str  # FAKE_LISTING | ALREADY_RENTED | ALREADY_SOLD | WRONG_INFORMATION | BROKER | DUPLICATE | SCAM | INAPPROPRIATE | OTHER
    description: str | None = Field(default=None, max_length=2000)
