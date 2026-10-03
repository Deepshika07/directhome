from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import forbidden, unauthorized
from app.core.security import decode_access_token
from app.models import Role, User, UserRole

bearer = HTTPBearer(auto_error=False)
optional_bearer = HTTPBearer(auto_error=False)


def _user_from_token(db: Session, token: str) -> User:
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (InvalidTokenError, KeyError, ValueError):
        raise unauthorized("Invalid or expired access token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized("Account not found or deactivated")
    return user


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise unauthorized()
    return _user_from_token(db, creds.credentials)


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    if creds is None:
        return None
    try:
        return _user_from_token(db, creds.credentials)
    except Exception:
        return None


def user_role_codes(db: Session, user_id: int) -> list[str]:
    return list(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    )


def require_roles(*codes: str):
    def checker(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        roles = user_role_codes(db, user.id)
        if not set(roles) & set(codes):
            raise forbidden(f"Requires role: {' or '.join(codes)}")
        return user

    return checker


require_seller = require_roles("SELLER", "ADMIN")
require_admin = require_roles("ADMIN")


def client_ip_hash(request: Request) -> str | None:
    from app.core.security import hash_token

    ip = request.client.host if request.client else None
    return hash_token(ip) if ip else None
