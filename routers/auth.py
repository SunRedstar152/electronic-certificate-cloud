from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from auth import (
    _invalid_credentials,
    create_access_token,
    get_current_user,
    get_password_hash,
    user_has_system_access,
    verify_password,
)
from config import settings
from database import get_db
from models import User
from services.login_throttle import login_attempt


router = APIRouter(tags=["Authentication"])
# One recommended-cost hash per worker prevents the unknown-user fast path.
# This value grants no access: a matching, active User is still required.
_DUMMY_PASSWORD_HASH = get_password_hash("nonexistent-login-account-timing-check")


class Token(BaseModel):
    access_token: str
    token_type: str


@router.post("/login", response_model=Token)
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # Forwarded headers are deliberately not consulted here. Only the ASGI
    # peer address supplied by the server selects the shared attempt budget.
    client_ip = request.client.host if request.client is not None else "unknown-peer"
    with login_attempt(db, form_data.username, client_ip) as attempt:
        user = db.scalar(
            select(User)
            .options(joinedload(User.organization))
            .where(User.username == form_data.username)
        )
        stored_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        password_valid = verify_password(form_data.password, stored_hash)
        authenticated = user is not None and password_valid and user_has_system_access(user)
        if authenticated:
            token = create_access_token(
                user,
                expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            )
            attempt.record_success()
        else:
            attempt.record_failure()

    # The context commits failed counters before emitting the usual 401.
    if not authenticated:
        raise _invalid_credentials()
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "role": current_user.role,
        "organization_id": current_user.organization_id,
        "is_active": current_user.is_active,
    }
