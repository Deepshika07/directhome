from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, user_role_codes
from app.core.database import get_db
from app.models import User
from app.schemas.auth import (
    LoginIn,
    RefreshIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from app.schemas.common import ok
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id, name=user.name, email=user.email, phone=user.phone,
        profile_image_url=user.profile_image_url,
        phone_verified=user.phone_verified,
        email_verified=user.email_verified,
        kyc_status=user.kyc_status.value,
        is_active=user.is_active,
        roles=user_role_codes(db, user.id),
        created_at=user.created_at,
    )


@router.post("/register", status_code=201)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    """Create an account (BUYER or SELLER) and return tokens."""
    user = auth_service.register(db, payload)
    tokens = auth_service.issue_tokens(db, user)
    db.commit()
    return ok({"user": _user_out(db, user), "tokens": tokens})


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    """Authenticate with phone or email + password."""
    user = auth_service.authenticate(db, payload.identifier, payload.password)
    tokens = auth_service.issue_tokens(db, user)
    db.commit()
    return ok({"user": _user_out(db, user), "tokens": tokens})


@router.post("/refresh")
def refresh(payload: RefreshIn, db: Session = Depends(get_db)):
    """Exchange a refresh token for a new token pair (old refresh is revoked)."""
    user = auth_service.rotate_refresh_token(db, payload.refresh_token)
    tokens = auth_service.issue_tokens(db, user)
    db.commit()
    return ok(tokens)


@router.post("/logout")
def logout(payload: RefreshIn, db: Session = Depends(get_db)):
    """Revoke a refresh token."""
    auth_service.revoke_refresh_token(db, payload.refresh_token)
    db.commit()
    return ok({"message": "Logged out"})


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Current user's profile, roles, and KYC status."""
    return ok(_user_out(db, user))
