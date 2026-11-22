from datetime import datetime

from pydantic import BaseModel, Field


class PlanIn(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    applies_to: str = Field(pattern="^(SELLER|BUYER)$")
    price_paise: int = Field(ge=0)
    duration_days: int | None = Field(default=None, ge=1)
    features: dict = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = 0


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_paise: int | None = Field(default=None, ge=0)
    duration_days: int | None = None
    features: dict | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class PlanOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    applies_to: str
    price_paise: int
    currency: str
    duration_days: int | None
    features: dict
    is_active: bool
    sort_order: int


class OrderIn(BaseModel):
    plan_id: int


class ConfirmIn(BaseModel):
    signature: str | None = None  # mock: pass "fail" to simulate failure


class GrantIn(BaseModel):
    user_id: int
    plan_id: int
    days: int | None = Field(default=None, ge=1)


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    plan_code: str
    plan_name: str
    status: str
    source: str
    starts_at: datetime
    expires_at: datetime | None
