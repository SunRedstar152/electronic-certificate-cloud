from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import func, select

from auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    require_admin,
    require_organization_user,
    user_has_system_access,
    verify_password,
)
from config import settings
from core.errors import api_error, register_error_handlers
from database import get_db
from models import LoginAttemptBucket, Organization, RecordStatus, User, UserRole
from routers.auth import router as auth_router
from services.login_throttle import bucket_keys


CORRECT_PASSWORD = "CorrectPass123!"


@pytest.fixture
def organization(db_session):
    value = Organization(name="Information Technology Center", code="AUTH-ITC")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, organization):
    value = User(
        username="auth-organization-user",
        password_hash=get_password_hash(CORRECT_PASSWORD),
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def inactive_user(db_session, organization):
    value = User(
        username="auth-inactive-user",
        password_hash=get_password_hash(CORRECT_PASSWORD),
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=False,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def admin_user(db_session):
    value = User(
        username="auth-admin-user",
        password_hash=get_password_hash(CORRECT_PASSWORD),
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def client(db_session):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    @app.get("/admin-only")
    def admin_only(user: User = Depends(require_admin)):
        return {"user_id": user.id}

    @app.get("/organization-only")
    def organization_only(user: User = Depends(require_organization_user)):
        return {"user_id": user.id}

    @app.get("/boom")
    def boom():
        raise RuntimeError("database password leaked in stack trace")

    return TestClient(app, raise_server_exceptions=False)


def login(client, user, password=CORRECT_PASSWORD):
    return client.post(
        "/login", data={"username": user.username, "password": password}
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_invalid_credentials(response):
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_password_hash_uses_recommended_algorithm():
    password_hash = get_password_hash(CORRECT_PASSWORD)

    assert password_hash.startswith("$argon2")
    assert verify_password(CORRECT_PASSWORD, password_hash) is True
    assert verify_password("WrongPass123!", password_hash) is False


def test_inactive_user_cannot_login(client, inactive_user):
    response = login(client, inactive_user)

    assert_invalid_credentials(response)


def test_login_rejects_active_user_when_organization_is_inactive(
    client, db_session, organization, organization_user
):
    organization.status = RecordStatus.inactive
    db_session.flush()

    assert_invalid_credentials(login(client, organization_user))


def test_existing_token_is_rejected_after_organization_deactivation(
    client, db_session, organization, organization_user
):
    token = login(client, organization_user).json()["access_token"]
    organization.status = RecordStatus.inactive
    db_session.flush()

    assert_invalid_credentials(
        client.get("/organization-only", headers=bearer(token))
    )


def test_organization_reactivation_preserves_individual_account_access_flags(
    client, db_session, inactive_user, organization, organization_user
):
    organization.status = RecordStatus.inactive
    db_session.flush()
    assert_invalid_credentials(login(client, organization_user))

    organization.status = RecordStatus.active
    db_session.flush()

    assert login(client, organization_user).status_code == 200
    assert_invalid_credentials(login(client, inactive_user))


def test_create_access_token_rejects_active_organization_user_without_relation():
    user = User(
        username="auth-orphaned-organization-user",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=999999,
        is_active=True,
    )

    with pytest.raises(ValueError, match="system access"):
        create_access_token(user)


def test_detached_organization_user_without_loaded_relation_is_denied_safely(
    db_session, organization_user
):
    user_id = organization_user.id
    db_session.expunge_all()
    detached_user = db_session.scalar(select(User).where(User.id == user_id))
    db_session.close()

    assert user_has_system_access(detached_user) is False
    with pytest.raises(ValueError, match="system access"):
        create_access_token(detached_user)


def test_admin_without_organization_remains_authenticated(client, admin_user):
    response = login(client, admin_user)

    assert response.status_code == 200
    current = client.get("/admin-only", headers=bearer(response.json()["access_token"]))
    assert current.status_code == 200


def test_malformed_login_uses_safe_validation_error_envelope(client):
    response = client.post(
        "/login",
        data={"username": "submitted-secret-value"},
        headers={"X-Correlation-ID": "request-validation-123"},
    )

    assert response.status_code == 422
    assert response.headers["X-Correlation-ID"] == "request-validation-123"
    assert response.json() == {
        "error": {
            "code": "REQUEST_VALIDATION_ERROR",
            "message": "The request contains invalid or missing fields.",
            "correlation_id": "request-validation-123",
        }
    }
    assert "submitted-secret-value" not in response.text


def test_login_token_contains_required_signed_claims(client, organization_user):
    response = login(client, organization_user)

    assert response.status_code == 200
    token = response.json()["access_token"]
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert claims["sub"] == str(organization_user.id)
    assert claims["role"] == "organization"
    assert claims["organization_id"] == organization_user.organization_id
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert claims["exp"] > claims["iat"]


def test_protected_request_requeries_user_and_enforces_deactivation(
    client, db_session, organization_user
):
    token = login(client, organization_user).json()["access_token"]
    organization_user.is_active = False
    db_session.flush()

    response = client.get("/organization-only", headers=bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_role_dependencies_reject_wrong_role(
    client, organization_user, admin_user
):
    organization_token = login(client, organization_user).json()["access_token"]
    admin_token = login(client, admin_user).json()["access_token"]

    admin_response = client.get("/admin-only", headers=bearer(organization_token))
    organization_response = client.get(
        "/organization-only", headers=bearer(admin_token)
    )

    for response in (admin_response, organization_response):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"
        assert response.json()["error"]["message"] == (
            "You do not have permission to perform this action."
        )
        assert response.json()["error"]["correlation_id"]


def test_api_error_handler_preserves_safe_error_and_correlation_id():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/forbidden")
    def forbidden():
        raise api_error(
            403,
            "AUTH_FORBIDDEN",
            "You do not have permission to perform this action.",
        )

    client = TestClient(app)
    response = client.get(
        "/forbidden", headers={"X-Correlation-ID": "request-auth-123"}
    )

    assert response.status_code == 403
    assert response.headers["X-Correlation-ID"] == "request-auth-123"
    assert response.json() == {
        "error": {
            "code": "AUTH_FORBIDDEN",
            "message": "You do not have permission to perform this action.",
            "correlation_id": "request-auth-123",
        }
    }


def test_unexpected_errors_do_not_expose_exception_details(client):
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["error"]["message"] == "An unexpected error occurred."
    assert "database password" not in response.text


def test_framework_http_errors_use_stable_safe_envelope(client):
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_ERROR"
    assert response.json()["error"]["message"] == (
        "The request could not be completed."
    )
    assert "Not Found" not in response.text


def test_create_access_token_rejects_inactive_user(inactive_user):
    with pytest.raises(ValueError, match="active user"):
        create_access_token(inactive_user)


def test_access_token_issued_at_is_current(admin_user):
    token = create_access_token(admin_user)
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    issued_at = datetime.fromtimestamp(claims["iat"], timezone.utc)
    assert abs((datetime.now(timezone.utc) - issued_at).total_seconds()) < 5


def login_buckets(db_session, username, peer="testclient"):
    ip_key, pair_key = bucket_keys(username, peer, settings.SECRET_KEY)
    return (
        db_session.get(LoginAttemptBucket, ip_key),
        db_session.get(LoginAttemptBucket, pair_key),
    )


def test_login_blocks_sixth_guess_including_a_correct_password(client, db_session, admin_user):
    for _ in range(5):
        assert_invalid_credentials(login(client, admin_user, "wrong"))

    response = login(client, admin_user)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "AUTH_RATE_LIMITED"
    assert 1 <= int(response.headers["Retry-After"]) <= 900
    ip, pair = login_buckets(db_session, admin_user.username)
    assert (ip.failed_attempts, pair.failed_attempts) == (5, 5)


def test_success_preserves_prior_ip_failures_in_postgresql(client, db_session, admin_user):
    for _ in range(4):
        assert_invalid_credentials(login(client, admin_user, "wrong"))
    assert login(client, admin_user).status_code == 200
    for _ in range(4):
        assert_invalid_credentials(login(client, admin_user, "wrong"))

    ip, pair = login_buckets(db_session, admin_user.username)
    assert (ip.failed_attempts, pair.failed_attempts) == (8, 4)


def test_ip_failure_budget_applies_across_usernames(client, db_session, admin_user):
    for index in range(30):
        response = client.post(
            "/login", data={"username": f"nonexistent-throttle-user-{index}", "password": "wrong"}
        )
        assert_invalid_credentials(response)

    # Even an unrelated valid login shares this peer's exhausted IP budget.
    response = login(client, admin_user)
    assert response.status_code == 429
    ip, pair = login_buckets(db_session, admin_user.username)
    assert ip.failed_attempts == 30
    assert pair is None  # A blocked IP cannot create unbounded new pair rows.


def test_login_window_expiry_allows_a_valid_login(client, db_session, admin_user):
    for _ in range(5):
        assert_invalid_credentials(login(client, admin_user, "wrong"))
    ip, pair = login_buckets(db_session, admin_user.username)
    pair.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.flush()

    response = login(client, admin_user)

    assert response.status_code == 200
    db_session.refresh(ip)
    db_session.refresh(pair)
    assert (ip.failed_attempts, pair.failed_attempts) == (5, 0)


def test_remote_guesses_do_not_lock_the_account_for_other_peers(client, admin_user):
    for _ in range(5):
        assert_invalid_credentials(login(client, admin_user, "wrong"))

    with TestClient(client.app, client=("198.51.100.81", 45678)) as other_peer:
        assert login(other_peer, admin_user).status_code == 200


def test_expired_login_cleanup_is_bounded(client, db_session, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_CLEANUP_BATCH_SIZE", 3)
    expired_keys = [f"{value:064x}" for value in range(1, 8)]
    db_session.add_all([
        LoginAttemptBucket(
            bucket_key=key,
            failed_attempts=5,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        for key in expired_keys
    ])
    db_session.flush()

    assert login(client, admin_user).status_code == 200

    remaining = db_session.scalar(
        select(func.count()).select_from(LoginAttemptBucket)
        .where(LoginAttemptBucket.bucket_key.in_(expired_keys))
    )
    assert remaining == 4
