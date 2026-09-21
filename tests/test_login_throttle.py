"""Login throttling policy tests that never connect to PostgreSQL.

Run with --noconftest and non-production DATABASE_URL / SECRET_KEY values.
The real PostgreSQL locking/commit paths are covered by the API integration suite.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from config import Settings


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
TEST_SECRET = "login-policy-unit-test-key-not-a-production-secret"


def policy_settings(**overrides):
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://unit_test@localhost/pkru_certificate",
        SECRET_KEY=TEST_SECRET,
        **overrides,
    )


def test_default_policy_limits_repeated_guesses():
    policy = policy_settings()

    assert policy.LOGIN_PAIR_MAX_FAILURES == 5
    assert policy.LOGIN_IP_MAX_FAILURES == 30
    assert policy.LOGIN_WINDOW_SECONDS == 900


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("LOGIN_PAIR_MAX_FAILURES", 0),
        ("LOGIN_IP_MAX_FAILURES", -1),
        ("LOGIN_WINDOW_SECONDS", 0),
        ("LOGIN_CLEANUP_BATCH_SIZE", 0),
        ("LOGIN_CLEANUP_BATCH_SIZE", 10001),
    ],
)
def test_invalid_policy_settings_cannot_disable_protection(field, value):
    with pytest.raises(ValidationError):
        policy_settings(**{field: value})


def bucket(failures=0, expires_at=NOW):
    from models import LoginAttemptBucket

    return LoginAttemptBucket(
        bucket_key="a" * 64,
        failed_attempts=failures,
        expires_at=expires_at,
    )


def test_bucket_identity_does_not_store_raw_username_or_ip():
    from services.login_throttle import bucket_keys

    ip_key, pair_key = bucket_keys("private-user", "203.0.113.21", TEST_SECRET)

    assert ip_key != pair_key
    assert all(len(key) == 64 and int(key, 16) >= 0 for key in (ip_key, pair_key))
    assert "private-user" not in pair_key
    assert "203.0.113.21" not in ip_key
    assert bucket_keys("private-user", "203.0.113.21", TEST_SECRET) == (ip_key, pair_key)
    assert bucket_keys("private-user", "203.0.113.21", TEST_SECRET + "rotated") != (ip_key, pair_key)


def test_different_users_share_only_the_ip_budget():
    from services.login_throttle import bucket_keys

    first = bucket_keys("user-a", "203.0.113.21", TEST_SECRET)
    second = bucket_keys("user-b", "203.0.113.21", TEST_SECRET)
    remote = bucket_keys("user-a", "203.0.113.22", TEST_SECRET)

    assert first[0] == second[0]
    assert first[1] != second[1]
    assert set(first).isdisjoint(remote)


@pytest.mark.parametrize(
    ("failures", "expires_in", "expected"),
    [(4, 900, 0), (5, 900, 900), (5, 0.1, 1), (5, 0, 0), (5, -1, 0)],
)
def test_retry_delay_obeys_failure_limit_and_exact_expiry(failures, expires_in, expected):
    from services.login_throttle import retry_after

    state = bucket(failures, NOW + timedelta(seconds=expires_in))

    assert retry_after(state, 5, NOW) == expected


def test_failure_counts_toward_both_budgets_without_extending_window():
    from services.login_throttle import LoginAttempt

    ip = bucket(9, NOW + timedelta(seconds=500))
    pair = bucket(3, NOW + timedelta(seconds=600))
    attempt = LoginAttempt(ip, pair, NOW, 900)

    attempt.record_failure()

    assert (ip.failed_attempts, pair.failed_attempts) == (10, 4)
    assert ip.expires_at == NOW + timedelta(seconds=500)
    assert pair.expires_at == NOW + timedelta(seconds=600)


def test_expired_budget_restarts_at_first_failure():
    from services.login_throttle import LoginAttempt

    ip = bucket(30, NOW)
    pair = bucket(5, NOW - timedelta(seconds=1))

    LoginAttempt(ip, pair, NOW, 900).record_failure()

    assert (ip.failed_attempts, pair.failed_attempts) == (1, 1)
    assert ip.expires_at == pair.expires_at == NOW + timedelta(seconds=900)


def test_success_clears_only_the_username_ip_budget():
    from services.login_throttle import LoginAttempt, retry_after

    ip = bucket(29, NOW + timedelta(seconds=700))
    pair = bucket(4, NOW + timedelta(seconds=600))

    LoginAttempt(ip, pair, NOW, 900).record_success()

    assert (ip.failed_attempts, pair.failed_attempts) == (29, 0)
    assert ip.expires_at == NOW + timedelta(seconds=700)
    assert retry_after(pair, 5, NOW) == 0


def test_rate_limited_response_has_safe_envelope_and_retry_header():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.errors import register_error_handlers
    from services.login_throttle import rate_limit_error

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/limited")
    def limited():
        raise rate_limit_error(17)

    response = TestClient(app).get("/limited")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    error = response.json()["error"]
    assert error["code"] == "AUTH_RATE_LIMITED"
    assert any("\u0e00" <= char <= "\u0e7f" for char in error["message"])
    assert error["correlation_id"]


@pytest.fixture
def login_api(monkeypatch):
    """Replace only DB access; use the production route, hashes and policy."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import get_password_hash
    from core.errors import register_error_handlers
    from database import get_db
    from models import User, UserRole
    from routers import auth as auth_routes
    from services.login_throttle import LoginAttempt, rate_limit_error

    user = User(
        id=123,
        username="known-user",
        password_hash=get_password_hash("CorrectPass123!"),
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    state = SimpleNamespace(
        user=user,
        ip=bucket(2, NOW + timedelta(seconds=900)),
        pair=bucket(2, NOW + timedelta(seconds=900)),
        committed=False,
        limited=False,
        peer=None,
        verified_hashes=[],
    )

    class UserSession:
        def scalar(self, _query):
            return state.user

    @contextmanager
    def attempt_context(_db, username, client_ip):
        state.peer = (username, client_ip)
        if state.limited:
            raise rate_limit_error(42)
        yield LoginAttempt(state.ip, state.pair, NOW, 900)
        state.committed = True

    original_verify = auth_routes.verify_password

    def tracked_verify(password, stored_hash):
        state.verified_hashes.append(stored_hash)
        return original_verify(password, stored_hash)

    monkeypatch.setattr(auth_routes, "login_attempt", attempt_context, raising=False)
    monkeypatch.setattr(auth_routes, "verify_password", tracked_verify)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_db] = lambda: UserSession()
    state.client = TestClient(app, client=("203.0.113.21", 43210))
    return state


@pytest.mark.parametrize("user_state", ["wrong-password", "nonexistent", "inactive"])
def test_login_rejection_commits_both_failure_counters(login_api, user_state):
    if user_state == "nonexistent":
        login_api.user = None
    elif user_state == "inactive":
        login_api.user.is_active = False
    password = "wrong" if user_state == "wrong-password" else "CorrectPass123!"

    response = login_api.client.post("/login", data={"username": "known-user", "password": password})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert (login_api.ip.failed_attempts, login_api.pair.failed_attempts) == (3, 3)
    assert login_api.committed
    # Unknown and inactive identities must still perform a real Argon2 check.
    assert len(login_api.verified_hashes) == 1
    assert login_api.verified_hashes[0].startswith("$argon2")


def test_login_success_clears_pair_but_preserves_shared_ip_history(login_api):
    response = login_api.client.post(
        "/login", data={"username": "known-user", "password": "CorrectPass123!"}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert (login_api.ip.failed_attempts, login_api.pair.failed_attempts) == (2, 0)
    assert login_api.committed


def test_limited_login_skips_password_verification_even_for_correct_password(login_api):
    login_api.limited = True

    response = login_api.client.post(
        "/login", data={"username": "known-user", "password": "CorrectPass123!"}
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "AUTH_RATE_LIMITED"
    assert response.headers["Retry-After"] == "42"
    assert login_api.verified_hashes == []


def test_forwarded_headers_do_not_choose_the_login_budget(login_api):
    response = login_api.client.post(
        "/login",
        data={"username": "known-user", "password": "CorrectPass123!"},
        headers={"X-Forwarded-For": "198.51.100.42", "Forwarded": "for=198.51.100.43"},
    )

    assert response.status_code == 200
    assert login_api.peer == ("known-user", "203.0.113.21")
