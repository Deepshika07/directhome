import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def normalize_phone(v: str) -> str:
    phone = re.sub(r"[\s\-()]", "", v)
    if not PHONE_RE.match(phone):
        raise ValueError("phone must be 10-15 digits, optionally prefixed with +")
    return phone


class RegisterIn(BaseModel):
    name: str
    phone: str
    email: EmailStr | None = None
    password: str
    role: str = "BUYER"  # BUYER or SELLER

    _phone = field_validator("phone")(normalize_phone)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        role = v.upper()
        if role not in {"BUYER", "SELLER"}:
            raise ValueError("role must be BUYER or SELLER")
        return role


class LoginIn(BaseModel):
    identifier: str  # phone or email
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token seconds


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    phone: str
    profile_image_url: str | None
    phone_verified: bool
    email_verified: bool
    kyc_status: str
    is_active: bool
    roles: list[str] = []
    created_at: datetime
