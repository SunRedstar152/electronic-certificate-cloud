from datetime import datetime, timedelta, timezone

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.exc import DetachedInstanceError

from config import settings
from core.errors import api_error
from database import get_db
from models import RecordStatus, User, UserRole


password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)


def verify_password(plain_password: str, stored_password_hash: str) -> bool:
    try:
        return password_hash.verify(plain_password, stored_password_hash)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)


def user_has_system_access(user: User) -> bool:
    try:
        if not user.is_active:
            return False
        if user.role == UserRole.organization:
            return (
                user.organization is not None
                and user.organization.status == RecordStatus.active
            )
        return True
    except DetachedInstanceError:
        return False


def create_access_token(
    user: User, expires_delta: timedelta | None = None
) -> str:
    if not user_has_system_access(user):
        raise ValueError("An access token requires an active user with system access.")

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    claims = {
        "sub": str(user.id),
        "role": user.role.value,
        "organization_id": user.organization_id,
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _invalid_credentials():
    error = api_error(
        401,
        "AUTH_INVALID_CREDENTIALS",
        "Invalid username or password.",
    )
    error.headers = {"WWW-Authenticate": "Bearer"}
    return error


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if token is None:
        raise _invalid_credentials()

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        subject = payload.get("sub")
        user_id = int(subject)
    except (JWTError, TypeError, ValueError):
        raise _invalid_credentials()

    user = db.scalar(
        select(User)
        .options(joinedload(User.organization))
        .where(User.id == user_id)
    )
    if user is None or not user_has_system_access(user):
        raise _invalid_credentials()
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise api_error(
            403,
            "AUTH_FORBIDDEN",
            "You do not have permission to perform this action.",
        )
    return current_user


def require_organization_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.organization:
        raise api_error(
            403,
            "AUTH_FORBIDDEN",
            "You do not have permission to perform this action.",
        )
    return current_user


# Temporary import compatibility for routers migrated in later tasks.
get_current_admin = require_admin
get_current_instructor = require_organization_user
