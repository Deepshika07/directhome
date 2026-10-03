import enum


class ListingType(str, enum.Enum):
    RENT = "RENT"
    SALE = "SALE"
    PG = "PG"
    SHARED = "SHARED"


class PropertyCategory(str, enum.Enum):
    RESIDENTIAL = "RESIDENTIAL"
    LAND = "LAND"
    COMMERCIAL = "COMMERCIAL"
    PG = "PG"


class Furnishing(str, enum.Enum):
    UNFURNISHED = "UNFURNISHED"
    SEMI_FURNISHED = "SEMI_FURNISHED"
    FULLY_FURNISHED = "FULLY_FURNISHED"


class PropertyStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RENTED = "RENTED"
    SOLD = "SOLD"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class AvailabilityStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    RECENTLY_VERIFIED = "RECENTLY_VERIFIED"
    EXPIRING = "EXPIRING"
    UNVERIFIED = "UNVERIFIED"
    EXPIRED = "EXPIRED"


class MediaType(str, enum.Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    FLOOR_PLAN = "FLOOR_PLAN"
    VIRTUAL_TOUR = "VIRTUAL_TOUR"


class KycStatus(str, enum.Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class DocumentAppliesTo(str, enum.Enum):
    USER = "USER"
    PROPERTY = "PROPERTY"
    BOTH = "BOTH"


class VerificationType(str, enum.Enum):
    OWNER = "OWNER"
    PHONE = "PHONE"
    AVAILABILITY = "AVAILABILITY"
    PROPERTY = "PROPERTY"
    DOCUMENT = "DOCUMENT"


class VerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class AvailabilityEventStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"


class ConfirmationMethod(str, enum.Enum):
    OWNER_APP = "OWNER_APP"
    PHONE = "PHONE"
    WHATSAPP = "WHATSAPP"
    ADMIN = "ADMIN"


class ContactMethod(str, enum.Enum):
    PHONE = "PHONE"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    PLATFORM = "PLATFORM"


class InquiryStatus(str, enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class ReportReason(str, enum.Enum):
    FAKE_LISTING = "FAKE_LISTING"
    ALREADY_RENTED = "ALREADY_RENTED"
    ALREADY_SOLD = "ALREADY_SOLD"
    WRONG_INFORMATION = "WRONG_INFORMATION"
    BROKER = "BROKER"
    DUPLICATE = "DUPLICATE"
    SCAM = "SCAM"
    INAPPROPRIATE = "INAPPROPRIATE"
    OTHER = "OTHER"


class ReportStatus(str, enum.Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class OtpChannel(str, enum.Enum):
    SMS = "SMS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"


class OtpPurpose(str, enum.Enum):
    REGISTRATION = "REGISTRATION"
    LOGIN = "LOGIN"
    PHONE_VERIFY = "PHONE_VERIFY"
    EMAIL_VERIFY = "EMAIL_VERIFY"
    PASSWORD_RESET = "PASSWORD_RESET"
