from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import conflict, unauthorized
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import RefreshToken, Role, User, UserRole
from app.schemas.auth import RegisterIn, TokenOut


def _role_codes(db: Session, user_id: int) -> list[str]:
    return list(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    )


def issue_tokens(db: Session, user: User) -> TokenOut:
    roles = _role_codes(db, user.id)
    raw, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return TokenOut(
        access_token=create_access_token(user.id, roles),
        refresh_token=raw,
        expires_in=settings.access_token_expire_minutes * 60,
    )


def register(db: Session, payload: RegisterIn) -> User:
    if db.scalar(select(User.id).where(User.phone == payload.phone)):
        raise conflict("PHONE_TAKEN", "An account with this phone already exists")
    if payload.email and db.scalar(
        select(User.id).where(User.email == payload.email.lower())
    ):
        raise conflict("EMAIL_TAKEN", "An account with this email already exists")

    user = User(
        name=payload.name.strip(),
        phone=payload.phone,
        email=payload.email.lower() if payload.email else None,
        password_hash=hash_password(payload.password),
    )
    role = db.scalar(select(Role).where(Role.code == payload.role))
    if role is None:
        raise conflict("ROLE_MISSING", f"Role {payload.role} is not seeded")
    user.roles.append(UserRole(role=role))
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, identifier: str, password: str) -> User:
    ident = identifier.strip().lower()
    user = db.scalar(
        select(User).where(or_(User.phone == identifier.strip(), User.email == ident))
    )
    if user is None or not user.password_hash:
        raise unauthorized("Invalid credentials")
    if not verify_password(password, user.password_hash):
        raise unauthorized("Invalid credentials")
    if not user.is_active:
        raise unauthorized("Account is deactivated")
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
    return user


def rotate_refresh_token(db: Session, raw_token: str) -> User:
    token = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    now = datetime.now(timezone.utc)
    if token is None or token.revoked_at is not None or token.expires_at < now:
        raise unauthorized("Invalid or expired refresh token")
    token.revoked_at = now
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise unauthorized("Account not found or deactivated")
    return user


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    token = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if token and token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.flush()
