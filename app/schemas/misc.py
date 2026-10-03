from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentIn(BaseModel):
    document_type_id: int
    file_url: str = Field(max_length=1000)
    property_id: int | None = None  # set when the doc belongs to a listing


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_type_id: int
    property_id: int | None
    file_url: str
    status: str
    rejection_reason: str | None
    verified_at: datetime | None
    created_at: datetime


class InquiryIn(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    contact_method: str = "PHONE"  # PHONE | WHATSAPP | EMAIL | PLATFORM


class InquiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    property_id: int
    buyer_id: int
    owner_id: int
    message: str | None
    contact_method: str
    status: str
    created_at: datetime


class InquiryCreated(BaseModel):
    inquiry: InquiryOut
    owner_contact: dict  # {"name": ..., "phone": ...} — revealed on inquiry


class LookupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class PropertyTypeOut(LookupOut):
    category: str


class RoomConfigurationOut(LookupOut):
    bedrooms: int


class DocumentTypeOut(LookupOut):
    applies_to: str


class FiltersOut(BaseModel):
    listing_types: list[str]
    furnishing_options: list[str]
    property_types: list[PropertyTypeOut]
    room_configurations: list[RoomConfigurationOut]
    amenities: list[LookupOut]
    document_types: list[DocumentTypeOut]


class LocationSuggestion(BaseModel):
    type: str  # CITY | LOCALITY
    label: str
    city: str
    locality: str | None = None
