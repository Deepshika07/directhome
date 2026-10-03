from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    AvailabilityStatus,
    Furnishing,
    ListingType,
    MediaType,
    PropertyStatus,
)


class Property(Base, TimestampMixin):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    listing_type: Mapped[ListingType] = mapped_column(
        Enum(ListingType, name="listing_type"), nullable=False
    )
    property_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("property_types.id"), nullable=False
    )
    room_configuration_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("room_configurations.id")
    )
    bathrooms: Mapped[int | None] = mapped_column(SmallInteger)
    carpet_area_sqft: Mapped[int | None] = mapped_column(Integer)
    built_up_area_sqft: Mapped[int | None] = mapped_column(Integer)
    plot_area_sqft: Mapped[int | None] = mapped_column(Integer)
    floor_number: Mapped[int | None] = mapped_column(SmallInteger)
    total_floors: Mapped[int | None] = mapped_column(SmallInteger)
    furnishing: Mapped[Furnishing | None] = mapped_column(
        Enum(Furnishing, name="furnishing")
    )
    rent_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    security_deposit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    maintenance_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), server_default="0"
    )
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_negotiable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    available_from: Mapped[date | None] = mapped_column(Date)
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(PropertyStatus, name="property_status"),
        nullable=False,
        server_default=PropertyStatus.DRAFT.value,
    )
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        Enum(AvailabilityStatus, name="availability_status"),
        nullable=False,
        server_default=AvailabilityStatus.UNVERIFIED.value,
    )
    availability_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    trust_score: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    is_owner_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    extra_attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    owner: Mapped["User"] = relationship(
        back_populates="properties", foreign_keys=[owner_id]
    )
    property_type: Mapped["PropertyType"] = relationship(
        back_populates="properties"
    )
    room_configuration: Mapped["RoomConfiguration | None"] = relationship(
        back_populates="properties"
    )
    location: Mapped["PropertyLocation | None"] = relationship(
        back_populates="property", cascade="all, delete-orphan", uselist=False
    )
    media: Mapped[list["PropertyMedia"]] = relationship(
        back_populates="property",
        cascade="all, delete-orphan",
        order_by="PropertyMedia.display_order",
    )
    amenities: Mapped[list["PropertyAmenity"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("rent_amount IS NULL OR rent_amount >= 0", name="rent_nonneg"),
        CheckConstraint(
            "security_deposit IS NULL OR security_deposit >= 0",
            name="deposit_nonneg",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price >= 0", name="sale_price_nonneg"
        ),
        CheckConstraint(
            "trust_score BETWEEN 0 AND 100", name="trust_score_range"
        ),
        CheckConstraint(
            "status = 'DRAFT' "
            "OR (listing_type = 'SALE' AND sale_price IS NOT NULL) "
            "OR (listing_type <> 'SALE' AND rent_amount IS NOT NULL)",
            name="price_required_when_listed",
        ),
        Index("ix_properties_owner_id", "owner_id"),
        Index("ix_properties_status", "status"),
        Index("ix_properties_listing_type", "listing_type"),
        Index("ix_properties_property_type_id", "property_type_id"),
        Index("ix_properties_rent_amount", "rent_amount"),
        Index("ix_properties_sale_price", "sale_price"),
        Index("ix_properties_available_from", "available_from"),
        Index("ix_properties_created_at", "created_at"),
    )


class PropertyLocation(Base):
    __tablename__ = "property_locations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("properties.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    locality: Mapped[str] = mapped_column(String(150), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(
        String(60), nullable=False, server_default="India"
    )
    landmark: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped["Property"] = relationship(back_populates="location")

    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="lat_range"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="lng_range",
        ),
        Index("ix_property_locations_city", "city"),
        Index("ix_property_locations_locality", "locality"),
        Index("ix_property_locations_state", "state"),
        Index("ix_property_locations_pincode", "pincode"),
        Index("ix_property_locations_city_locality", "city", "locality"),
    )


class PropertyMedia(Base):
    __tablename__ = "property_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, name="media_type"),
        nullable=False,
        server_default=MediaType.IMAGE.value,
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000))
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped["Property"] = relationship(back_populates="media")

    __table_args__ = (
        Index("ix_property_media_property_id", "property_id"),
        Index(
            "uq_property_media_one_primary",
            "property_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class PropertyAmenity(Base):
    __tablename__ = "property_amenities"

    property_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("properties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    amenity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("amenities.id", ondelete="CASCADE"),
        primary_key=True,
    )

    property: Mapped["Property"] = relationship(back_populates="amenities")
    amenity: Mapped["Amenity"] = relationship()
