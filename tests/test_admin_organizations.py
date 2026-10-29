from datetime import datetime, timedelta, timezone
import os
import re
import threading
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from auth import create_access_token, get_password_hash, verify_password
from core.errors import register_error_handlers
from database import get_db
from models import (
    AuditLog,
    Batch,
    Certificate,
    Organization,
    RecordStatus,
    User,
    UserRole,
)
from routers.admin import router as admin_router
from services.accounts import set_organization_user_status


ADMIN_PASSWORD = "AdminPass123!"
ORGANIZATION_PASSWORD = "OrganizationPass123!"


def wait_for_postgres_backend_blocked(
    sessions, blocked_pid: int, blocking_pid: int, timeout: float = 5
) -> bool:
    deadline = time.monotonic() + timeout
    observer = sessions()
    try:
        while time.monotonic() < deadline:
            blockers = observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": blocked_pid}
            )
            if blocking_pid in blockers:
                return True
            time.sleep(0.02)
    finally:
        observer.close()
    return False


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def admin_user(db_session):
    value = User(
        username="organization-api-admin",
        password_hash=get_password_hash(ADMIN_PASSWORD),
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def existing_organization(db_session):
    value = Organization(
        name="Existing Organization",
        code="EXISTING",
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, existing_organization):
    value = User(
        username="existing-organization-user",
        password_hash=get_password_hash(ORGANIZATION_PASSWORD),
        role=UserRole.organization,
        organization_id=existing_organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def successor_user(db_session, existing_organization):
    value = User(
        username="existing-successor-user",
        password_hash=get_password_hash("SuccessorPass123!"),
        role=UserRole.organization,
        organization_id=existing_organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization_user(db_session):
    organization = Organization(name="Other Organization", code="OTHER-ORG")
    db_session.add(organization)
    db_session.flush()
    value = User(
        username="other-organization-user",
        password_hash=get_password_hash(ORGANIZATION_PASSWORD),
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def app(db_session):
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(admin_router)

    def override_get_db():
        yield db_session

    value.dependency_overrides[get_db] = override_get_db
    return value


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_client(client, admin_user):
    client.headers.update(authorization(admin_user))
    return client


@pytest.fixture
def organization_client(client, organization_user):
    client.headers.update(authorization(organization_user))
    return client


def organization_payload(**overrides):
    value = {
        "name": "Information Technology Center",
        "username": "itc-organization-account",
        "password": "StrongPass123!",
    }
    value.update(overrides)
    return value


def test_admin_creates_organization_with_hidden_generated_code(admin_client, db_session):
    response = admin_client.post(
        "/admin/organizations", json=organization_payload()
    )

    assert response.status_code == 201
    assert "code" not in response.json()["organization"]
    assert response.json()["account"]["username"] == "itc-organization-account"
    assert response.json()["account"]["role"] == "organization"
    assert "password" not in response.text.lower()

    organization = db_session.scalar(
        select(Organization).where(Organization.name == "Information Technology Center")
    )
    account = db_session.scalar(
        select(User).where(User.username == "itc-organization-account")
    )
    assert re.fullmatch(r"ORG[A-Z0-9]{13}", organization.code)
    assert account.organization_id == organization.id

    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "organization.create")
    )
    assert audit.organization_id == organization.id
    assert audit.details == {"username": "itc-organization-account"}
    assert "code" not in audit.details


def test_organization_user_is_forbidden(organization_client):
    response = organization_client.get("/admin/organizations")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("post", "/admin/organizations", organization_payload(code="client-code")),
        ("patch", "/admin/organizations/{organization_id}", {"code": "client-code"}),
    ],
)
def test_organization_code_is_not_client_controllable(
    admin_client, existing_organization, method, url, payload
):
    response = getattr(admin_client, method)(
        url.format(organization_id=existing_organization.id), json=payload
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_create_rejects_normalized_duplicate_name_without_partial_rows(
    admin_client, db_session, existing_organization
):
    before = {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    }

    response = admin_client.post(
        "/admin/organizations",
        json=organization_payload(
            name="  Existing\tOrganization  ", username="successor-not-created"
        ),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORG_NAME_EXISTS"
    assert {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    } == before


def test_create_rejects_duplicate_username_without_partial_rows(admin_client, db_session):
    db_session.add(
        User(
            username="duplicate-username",
            password_hash="hash",
            role=UserRole.admin,
            organization_id=None,
        )
    )
    db_session.flush()
    before = {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    }

    response = admin_client.post(
        "/admin/organizations",
        json=organization_payload(username="duplicate-username"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_USERNAME_EXISTS"
    assert {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    } == before


def test_generated_code_collision_retries_then_succeeds(
    admin_client, db_session, monkeypatch
):
    db_session.add(Organization(name="Existing Code Owner", code="ORGAAAAAAAAAAAAA"))
    db_session.flush()
    candidates = iter(["ORGAAAAAAAAAAAAA", "ORGBBBBBBBBBBBBB"])
    monkeypatch.setattr(
        "services.accounts.generate_organization_code", lambda: next(candidates)
    )

    response = admin_client.post("/admin/organizations", json=organization_payload())

    assert response.status_code == 201
    created = db_session.scalar(
        select(Organization).where(Organization.name == "Information Technology Center")
    )
    assert created.code == "ORGBBBBBBBBBBBBB"


def test_generated_code_collision_exhaustion_is_atomic(admin_client, db_session, monkeypatch):
    db_session.add(Organization(name="Existing Code Owner", code="ORGAAAAAAAAAAAAA"))
    db_session.flush()
    attempts = 0

    def colliding_code():
        nonlocal attempts
        attempts += 1
        return "ORGAAAAAAAAAAAAA"

    monkeypatch.setattr(
        "services.accounts.generate_organization_code", colliding_code
    )
    before = {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    }

    response = admin_client.post("/admin/organizations", json=organization_payload())

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "ORG_CODE_GENERATION_FAILED"
    assert attempts == 5
    assert {
        "organizations": db_session.scalar(select(func.count()).select_from(Organization)),
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    } == before


def test_organization_list_collapses_personnel_and_reports_role_scoped_counts(
    admin_client, db_session, existing_organization, organization_user
):
    db_session.add_all(
        [
            User(
                username="active-successor",
                password_hash="hash",
                role=UserRole.organization,
                organization_id=existing_organization.id,
                is_active=True,
            ),
            User(
                username="inactive-predecessor",
                password_hash="hash",
                role=UserRole.organization,
                organization_id=existing_organization.id,
                is_active=False,
            ),
            User(
                username="unrelated-admin",
                password_hash="hash",
                role=UserRole.admin,
                organization_id=None,
                is_active=True,
            ),
        ]
    )
    db_session.flush()

    response = admin_client.get(
        "/admin/organizations", params={"search": "  INACTIVE-PREDECESSOR  "}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["organization"]["id"] == existing_organization.id
    assert body["items"][0]["personnel_count"] == 3
    assert body["items"][0]["active_personnel_count"] == 2
    assert set(body["items"][0]) == {
        "organization",
        "personnel_count",
        "active_personnel_count",
    }
    assert "account" not in body["items"][0]
    assert "code" not in response.text
    assert existing_organization.code not in response.text


def test_organization_list_searches_names_and_usernames_without_codes(
    admin_client, db_session
):
    named_organization = Organization(
        name="Searchable Organization", code="HIDDEN-NAME"
    )
    username_organization = Organization(
        name="Different Name", code="HIDDEN-USERNAME"
    )
    collapsed_whitespace_organization = Organization(
        name="Research Center", code="HIDDEN-WHITESPACE"
    )
    nfc_organization = Organization(name="NFC Café", code="HIDDEN-NFC")
    db_session.add_all(
        [
            named_organization,
            username_organization,
            collapsed_whitespace_organization,
            nfc_organization,
        ]
    )
    db_session.flush()
    db_session.add(
        User(
            username="inactive-search-personnel",
            password_hash="hash",
            role=UserRole.organization,
            organization_id=username_organization.id,
            is_active=False,
        )
    )
    db_session.flush()

    name_match = admin_client.get(
        "/admin/organizations", params={"search": " searchable organization "}
    )
    username_match = admin_client.get(
        "/admin/organizations", params={"search": "INACTIVE-SEARCH-PERSONNEL"}
    )
    code_match = admin_client.get(
        "/admin/organizations", params={"search": "HIDDEN-USERNAME"}
    )
    collapsed_whitespace_match = admin_client.get(
        "/admin/organizations", params={"search": "Research    Center"}
    )
    decomposed_unicode_match = admin_client.get(
        "/admin/organizations", params={"search": "NFC Cafe\u0301"}
    )

    assert [item["organization"]["id"] for item in name_match.json()["items"]] == [
        named_organization.id
    ]
    assert [item["organization"]["id"] for item in username_match.json()["items"]] == [
        username_organization.id
    ]
    assert code_match.json()["total"] == 0
    assert [
        item["organization"]["id"]
        for item in collapsed_whitespace_match.json()["items"]
    ] == [collapsed_whitespace_organization.id]
    assert [
        item["organization"]["id"]
        for item in decomposed_unicode_match.json()["items"]
    ] == [nfc_organization.id]


def test_organization_list_keeps_zero_user_rows_and_paginates_organizations(
    admin_client, db_session
):
    organizations = [
        Organization(name="zulu Paged", code="ZULU-PAGED"),
        Organization(name="Alpha Paged", code="ALPHA-PAGED"),
        Organization(
            name="Bravo Paged",
            code="BRAVO-PAGED",
            status=RecordStatus.inactive,
        ),
    ]
    db_session.add_all(organizations)
    db_session.flush()
    db_session.add_all(
        [
            User(
                username=f"zulu-personnel-{number}",
                password_hash="hash",
                role=UserRole.organization,
                organization_id=organizations[0].id,
                is_active=True,
            )
            for number in range(3)
        ]
    )
    db_session.flush()

    response = admin_client.get(
        "/admin/organizations",
        params={"search": "paged", "page": 2, "page_size": 1},
    )
    inactive = admin_client.get(
        "/admin/organizations", params={"search": "paged", "status": "inactive"}
    )
    zero_user = admin_client.get(
        "/admin/organizations", params={"search": "Alpha Paged"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 1
    assert [item["organization"]["id"] for item in body["items"]] == [
        organizations[2].id
    ]
    assert [item["organization"]["name"] for item in body["items"]] == [
        "Bravo Paged"
    ]
    assert [item["organization"]["id"] for item in inactive.json()["items"]] == [
        organizations[2].id
    ]
    assert zero_user.json()["items"][0]["personnel_count"] == 0
    assert zero_user.json()["items"][0]["active_personnel_count"] == 0


def test_list_clamps_page_size_to_one_hundred(admin_client):
    response = admin_client.get(
        "/admin/organizations", params={"page_size": 1000}
    )

    assert response.status_code == 200
    assert response.json()["page_size"] == 100


def test_admin_updates_organization_and_status_without_changing_personnel_flags(
    admin_client, db_session, existing_organization, organization_user
):
    inactive_personnel = User(
        username="already-inactive-personnel",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=existing_organization.id,
        is_active=False,
    )
    db_session.add(inactive_personnel)
    db_session.flush()

    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}",
        json={
            "name": "  Renamed\tOrganization  ",
            "contact_name": "Registrar",
            "status": "inactive",
        },
    )

    assert response.status_code == 200
    assert response.json()["organization"]["name"] == "Renamed Organization"
    assert "code" not in response.json()["organization"]
    assert response.json()["organization"]["status"] == "inactive"
    assert "account" not in response.json()
    db_session.refresh(organization_user)
    db_session.refresh(inactive_personnel)
    assert organization_user.is_active is True
    assert inactive_personnel.is_active is False
    assert set(
        db_session.scalars(
            select(AuditLog.action).where(
                AuditLog.organization_id == existing_organization.id
            )
        )
    ) == {"organization.update", "organization.status_update"}
    update_audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "organization.update")
    )
    assert update_audit.details == {"fields": ["contact_name", "name"]}


def test_update_rejects_unknown_organization(admin_client):
    response = admin_client.patch(
        "/admin/organizations/999999", json={"name": "Missing"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORG_NOT_FOUND"


def test_update_rejects_duplicate_normalized_name_without_mutating_existing_code(
    admin_client, db_session, existing_organization
):
    db_session.add(Organization(name="Other Organization", code="OTHER-CODE"))
    db_session.flush()

    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}",
        json={"name": "  other\torganization "},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORG_NAME_EXISTS"
    db_session.refresh(existing_organization)
    assert existing_organization.code == "EXISTING"


@pytest.mark.parametrize("field", ["name", "status"])
def test_update_rejects_explicit_null_required_fields_with_stable_envelope(
    admin_client, existing_organization, field
):
    correlation_id = f"null-{field}-request"

    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}",
        json={field: None},
        headers={"X-Correlation-ID": correlation_id},
    )

    assert response.status_code == 422
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json() == {
        "error": {
            "code": "REQUEST_VALIDATION_ERROR",
            "message": "The request contains invalid or missing fields.",
            "correlation_id": correlation_id,
        }
    }


def test_organization_password_reset_route_has_been_removed(
    admin_client, existing_organization
):
    response = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/reset-password",
        json={"password": "NewStrongPass456!"},
    )

    assert response.status_code == 404


def test_admin_lists_and_adds_personnel_under_the_same_organization(
    admin_client, db_session, existing_organization, organization_user
):
    first = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users",
        json={"username": "Zulu-Personnel", "password": "SuccessorPass123!"},
    )
    second = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users",
        json={"username": "alpha-personnel", "password": "SuccessorPass123!"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["account"]["organization_id"] == existing_organization.id
    assert first.json()["account"]["role"] == "organization"
    assert "password" not in first.text.lower()

    listing = admin_client.get(f"/admin/organizations/{existing_organization.id}/users")

    assert listing.status_code == 200
    assert [item["username"] for item in listing.json()["items"]] == [
        "alpha-personnel",
        "existing-organization-user",
        "Zulu-Personnel",
    ]
    assert listing.json()["total"] == 3
    assert all("password" not in item for item in listing.json()["items"])
    assert db_session.scalar(
        select(func.count()).select_from(User).where(
            User.organization_id == existing_organization.id,
            User.role == UserRole.organization,
        )
    ) == 3


def test_personnel_routes_reject_unknown_organization_and_forbid_extra_fields(
    admin_client,
):
    missing = admin_client.get("/admin/organizations/999999/users")
    invalid = admin_client.post(
        "/admin/organizations/999999/users",
        json={
            "username": "cannot-create",
            "password": "SuccessorPass123!",
            "role": "admin",
        },
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ORG_NOT_FOUND"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_personnel_requests_strip_usernames_and_reject_ownership_or_role_fields(
    admin_client, existing_organization, organization_user
):
    create = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users",
        json={"username": "  stripped-personnel  ", "password": "SuccessorPass123!"},
    )
    status = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}",
        json={"is_active": True, "organization_id": existing_organization.id},
    )
    password = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}/reset-password",
        json={"password": "NeverStoredPass123!", "role": "admin"},
    )

    assert create.status_code == 201
    assert create.json()["account"]["username"] == "stripped-personnel"
    assert status.status_code == 422
    assert password.status_code == 422
    assert status.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert password.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("get", "", None),
        ("post", "", {"username": "forbidden-personnel", "password": "SuccessorPass123!"}),
        ("patch", "/{user_id}", {"is_active": False}),
        (
            "post",
            "/{user_id}/reset-password",
            {"password": "ForbiddenResetPass123!"},
        ),
    ],
)
def test_personnel_routes_require_an_administrator(
    organization_client, existing_organization, organization_user, method, suffix, payload
):
    request = getattr(organization_client, method)
    path = (
        f"/admin/organizations/{existing_organization.id}/users"
        f"{suffix.format(user_id=organization_user.id)}"
    )
    response = request(path) if payload is None else request(path, json=payload)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_admin_deactivates_only_the_selected_personnel_account(
    admin_client, db_session, existing_organization, organization_user, successor_user
):
    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}",
        json={"is_active": False},
    )

    assert response.status_code == 200
    db_session.refresh(organization_user)
    db_session.refresh(successor_user)
    assert organization_user.is_active is False
    assert successor_user.is_active is True
    assert response.json()["account"]["id"] == organization_user.id


def test_admin_cannot_deactivate_the_last_active_personnel(
    admin_client, existing_organization, organization_user
):
    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}",
        json={"is_active": False},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_LAST_ACTIVE"


def test_user_mutations_reject_accounts_outside_the_path_organization(
    admin_client,
    db_session,
    existing_organization,
    other_organization_user,
    admin_user,
):
    for method, suffix, payload in [
        ("patch", "", {"is_active": False}),
        ("post", "/reset-password", {"password": "NeverAppliedPass123!"}),
    ]:
        response = getattr(admin_client, method)(
            f"/admin/organizations/{existing_organization.id}/users/"
            f"{other_organization_user.id}{suffix}",
            json=payload,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{admin_user.id}",
        json={"is_active": False},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    db_session.refresh(other_organization_user)
    assert other_organization_user.is_active is True


def test_personnel_status_noop_writes_no_audit(
    admin_client, db_session, existing_organization, organization_user
):
    response = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}",
        json={"is_active": True},
    )

    assert response.status_code == 200
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "personnel.status_update"
        )
    ) == 0


def test_personnel_reset_changes_only_selected_password_without_leaking_secret(
    admin_client, db_session, existing_organization, organization_user, successor_user
):
    previous_successor_hash = successor_user.password_hash
    response = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users/{organization_user.id}/reset-password",
        json={"password": "OnlyThisAccountPass123!"},
    )

    assert response.status_code == 200
    db_session.refresh(organization_user)
    db_session.refresh(successor_user)
    assert verify_password("OnlyThisAccountPass123!", organization_user.password_hash)
    assert successor_user.password_hash == previous_successor_hash
    assert "OnlyThisAccountPass123!" not in response.text
    assert organization_user.password_hash not in response.text


def test_personnel_duplicate_create_is_atomic_and_mutations_record_safe_audits(
    admin_client, db_session, admin_user, existing_organization, organization_user
):
    correlation_id = "personnel-audit-test"
    create = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users",
        json={"username": "audited-successor", "password": "SuccessorPass123!"},
        headers={"X-Correlation-ID": correlation_id},
    )
    assert create.status_code == 201
    target_id = create.json()["account"]["id"]

    status_update = admin_client.patch(
        f"/admin/organizations/{existing_organization.id}/users/{target_id}",
        json={"is_active": False},
        headers={"X-Correlation-ID": correlation_id},
    )
    password_reset = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users/{target_id}/reset-password",
        json={"password": "AuditedResetPass123!"},
        headers={"X-Correlation-ID": correlation_id},
    )
    before = {
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    }
    duplicate = admin_client.post(
        f"/admin/organizations/{existing_organization.id}/users",
        json={"username": "audited-successor", "password": "DuplicatePass123!"},
        headers={"X-Correlation-ID": correlation_id},
    )

    assert status_update.status_code == 200
    assert password_reset.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ACCOUNT_USERNAME_EXISTS"
    assert {
        "users": db_session.scalar(select(func.count()).select_from(User)),
        "audits": db_session.scalar(select(func.count()).select_from(AuditLog)),
    } == before
    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == existing_organization.id)
            .order_by(AuditLog.id)
        )
    )
    assert {audit.action for audit in audits} == {
        "personnel.create",
        "personnel.status_update",
        "personnel.password_reset",
    }
    for audit in audits:
        assert audit.actor_id == admin_user.id
        assert audit.target_id == str(target_id)
        assert audit.correlation_id == correlation_id
        assert audit.details["username"] == "audited-successor"
        assert "password" not in str(audit.details).lower()
    status_audit = next(audit for audit in audits if audit.action == "personnel.status_update")
    assert status_audit.details == {
        "username": "audited-successor",
        "from": True,
        "to": False,
    }


def test_concurrent_personnel_deactivation_keeps_one_active_account():
    engine = create_engine(os.environ["TEST_DATABASE_URL"], pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, class_=Session)
    suffix = uuid4().hex[:8]
    first_active_query = threading.Event()
    release_first_query = threading.Event()
    second_backend_pid_ready = threading.Event()
    first_query_seen = False
    listener_lock = threading.Lock()
    outcomes = {}
    first_backend_pid = None
    second_backend_pid = None
    organization_id = None
    actor_id = None

    def hold_first_active_query(conn, cursor, statement, parameters, context, executemany):
        nonlocal first_query_seen
        if "FROM users" not in statement or "users.is_active IS true" not in statement:
            return
        with listener_lock:
            should_hold = not first_query_seen
            first_query_seen = True
        if should_hold:
            first_active_query.set()
            assert release_first_query.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", hold_first_active_query)
    try:
        setup = sessions()
        try:
            organization = Organization(
                name=f"Concurrent Personnel {suffix}", code=f"CONC-{suffix}"
            )
            actor = User(
                username=f"concurrent-admin-{suffix}",
                password_hash=get_password_hash(ADMIN_PASSWORD),
                role=UserRole.admin,
                organization_id=None,
                is_active=True,
            )
            accounts = [
                User(
                    username=f"concurrent-personnel-{suffix}-{number}",
                    password_hash=get_password_hash(ORGANIZATION_PASSWORD),
                    role=UserRole.organization,
                    organization=organization,
                    is_active=True,
                )
                for number in (1, 2)
            ]
            setup.add_all([organization, actor, *accounts])
            setup.flush()
            organization_id = organization.id
            actor_id = actor.id
            account_ids = [account.id for account in accounts]
            setup.commit()
        finally:
            setup.close()

        def deactivate(user_id, label):
            nonlocal first_backend_pid, second_backend_pid
            session = sessions()
            try:
                session.connection().info["personnel_status_worker"] = label
                actor = session.get(User, actor_id)
                if label == "first":
                    first_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                else:
                    second_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    second_backend_pid_ready.set()
                set_organization_user_status(
                    session,
                    organization_id,
                    user_id,
                    False,
                    actor,
                )
                session.commit()
                outcomes[label] = "deactivated"
            except Exception as exc:
                session.rollback()
                detail = getattr(exc, "detail", None)
                outcomes[label] = (
                    detail.get("code", type(exc).__name__)
                    if isinstance(detail, dict)
                    else type(exc).__name__
                )
            finally:
                session.close()

        first = threading.Thread(target=deactivate, args=(account_ids[0], "first"))
        second = threading.Thread(target=deactivate, args=(account_ids[1], "second"))
        first.start()
        assert first_active_query.wait(timeout=5)
        second.start()
        assert second_backend_pid_ready.wait(timeout=5)
        assert wait_for_postgres_backend_blocked(
            sessions, second_backend_pid, first_backend_pid
        )
        release_first_query.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive()
        assert not second.is_alive()
        assert sorted(outcomes.values()) == ["ACCOUNT_LAST_ACTIVE", "deactivated"]
        verify = sessions()
        try:
            assert verify.scalar(
                select(func.count()).select_from(User).where(
                    User.organization_id == organization_id,
                    User.role == UserRole.organization,
                    User.is_active.is_(True),
                )
            ) == 1
        finally:
            verify.close()
    finally:
        release_first_query.set()
        event.remove(engine, "after_cursor_execute", hold_first_active_query)
        cleanup = sessions()
        try:
            if organization_id is not None:
                cleanup.execute(
                    delete(AuditLog).where(AuditLog.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(User).where(User.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(Organization).where(Organization.id == organization_id)
                )
            if actor_id is not None:
                cleanup.execute(delete(User).where(User.id == actor_id))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def test_concurrent_same_personnel_deactivation_is_an_audited_noop_after_first_write():
    engine = create_engine(os.environ["TEST_DATABASE_URL"], pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, class_=Session)
    suffix = uuid4().hex[:8]
    first_active_query = threading.Event()
    release_first_query = threading.Event()
    second_backend_pid_ready = threading.Event()
    first_query_seen = False
    listener_lock = threading.Lock()
    outcomes = {}
    first_backend_pid = None
    second_backend_pid = None
    organization_id = None
    actor_id = None

    def hold_first_active_query(conn, cursor, statement, parameters, context, executemany):
        nonlocal first_query_seen
        if "FROM users" not in statement or "users.is_active IS true" not in statement:
            return
        with listener_lock:
            should_hold = not first_query_seen
            first_query_seen = True
        if should_hold:
            first_active_query.set()
            assert release_first_query.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", hold_first_active_query)
    try:
        setup = sessions()
        try:
            organization = Organization(
                name=f"Same Personnel {suffix}", code=f"SAME-{suffix}"
            )
            actor = User(
                username=f"same-personnel-admin-{suffix}",
                password_hash=get_password_hash(ADMIN_PASSWORD),
                role=UserRole.admin,
                organization_id=None,
                is_active=True,
            )
            accounts = [
                User(
                    username=f"same-personnel-{suffix}-{number}",
                    password_hash=get_password_hash(ORGANIZATION_PASSWORD),
                    role=UserRole.organization,
                    organization=organization,
                    is_active=True,
                )
                for number in (1, 2)
            ]
            setup.add_all([organization, actor, *accounts])
            setup.flush()
            organization_id = organization.id
            actor_id = actor.id
            target_id = accounts[0].id
            other_id = accounts[1].id
            setup.commit()
        finally:
            setup.close()

        def deactivate(label):
            nonlocal first_backend_pid, second_backend_pid
            session = sessions()
            try:
                session.connection().info["personnel_status_worker"] = label
                actor = session.get(User, actor_id)
                if label == "first":
                    first_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                else:
                    second_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    second_backend_pid_ready.set()
                set_organization_user_status(
                    session,
                    organization_id,
                    target_id,
                    False,
                    actor,
                )
                session.commit()
                outcomes[label] = "deactivated"
            except Exception as exc:
                session.rollback()
                detail = getattr(exc, "detail", None)
                outcomes[label] = (
                    detail.get("code", type(exc).__name__)
                    if isinstance(detail, dict)
                    else type(exc).__name__
                )
            finally:
                session.close()

        first = threading.Thread(target=deactivate, args=("first",))
        second = threading.Thread(target=deactivate, args=("second",))
        first.start()
        assert first_active_query.wait(timeout=5)
        second.start()
        assert second_backend_pid_ready.wait(timeout=5)
        assert wait_for_postgres_backend_blocked(
            sessions, second_backend_pid, first_backend_pid
        )
        release_first_query.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive()
        assert not second.is_alive()
        assert sorted(outcomes.values()) == ["deactivated", "deactivated"]
        verify = sessions()
        try:
            assert verify.scalar(select(User.is_active).where(User.id == target_id)) is False
            assert verify.scalar(select(User.is_active).where(User.id == other_id)) is True
            assert verify.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.action == "personnel.status_update",
                    AuditLog.target_id == str(target_id),
                )
            ) == 1
        finally:
            verify.close()
    finally:
        release_first_query.set()
        event.remove(engine, "after_cursor_execute", hold_first_active_query)
        cleanup = sessions()
        try:
            if organization_id is not None:
                cleanup.execute(
                    delete(AuditLog).where(AuditLog.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(User).where(User.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(Organization).where(Organization.id == organization_id)
                )
            if actor_id is not None:
                cleanup.execute(delete(User).where(User.id == actor_id))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def test_concurrent_same_personnel_reactivation_is_an_audited_noop_after_first_write():
    engine = create_engine(os.environ["TEST_DATABASE_URL"], pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, class_=Session)
    suffix = uuid4().hex[:8]
    first_target_lock = threading.Event()
    release_first_lock = threading.Event()
    second_backend_pid_ready = threading.Event()
    outcomes = {}
    organization_id = None
    actor_id = None
    first_backend_pid = None
    second_backend_pid = None

    def hold_first_target_lock(conn, cursor, statement, parameters, context, executemany):
        if (
            conn.info.get("personnel_status_worker") == "first"
            and "FROM users" in statement
            and "FOR UPDATE" in statement
        ):
            first_target_lock.set()
            assert release_first_lock.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", hold_first_target_lock)
    try:
        setup = sessions()
        try:
            organization = Organization(
                name=f"Same Reactivation {suffix}", code=f"REACT-{suffix}"
            )
            actor = User(
                username=f"same-reactivation-admin-{suffix}",
                password_hash=get_password_hash(ADMIN_PASSWORD),
                role=UserRole.admin,
                organization_id=None,
                is_active=True,
            )
            target = User(
                username=f"same-reactivation-target-{suffix}",
                password_hash=get_password_hash(ORGANIZATION_PASSWORD),
                role=UserRole.organization,
                organization=organization,
                is_active=False,
            )
            other = User(
                username=f"same-reactivation-other-{suffix}",
                password_hash=get_password_hash(ORGANIZATION_PASSWORD),
                role=UserRole.organization,
                organization=organization,
                is_active=True,
            )
            setup.add_all([organization, actor, target, other])
            setup.flush()
            organization_id = organization.id
            actor_id = actor.id
            target_id = target.id
            other_id = other.id
            setup.commit()
        finally:
            setup.close()

        def reactivate(label):
            nonlocal first_backend_pid, second_backend_pid
            session = sessions()
            try:
                session.connection().info["personnel_status_worker"] = label
                actor = session.get(User, actor_id)
                if label == "first":
                    first_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                else:
                    second_backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                    second_backend_pid_ready.set()
                set_organization_user_status(
                    session,
                    organization_id,
                    target_id,
                    True,
                    actor,
                )
                session.commit()
                outcomes[label] = "reactivated"
            except Exception as exc:
                session.rollback()
                detail = getattr(exc, "detail", None)
                outcomes[label] = (
                    detail.get("code", type(exc).__name__)
                    if isinstance(detail, dict)
                    else type(exc).__name__
                )
            finally:
                session.close()

        first = threading.Thread(target=reactivate, args=("first",))
        second = threading.Thread(target=reactivate, args=("second",))
        first.start()
        assert first_target_lock.wait(timeout=5)
        second.start()
        assert second_backend_pid_ready.wait(timeout=5)
        assert wait_for_postgres_backend_blocked(
            sessions, second_backend_pid, first_backend_pid
        )
        release_first_lock.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive()
        assert not second.is_alive()
        assert sorted(outcomes.values()) == ["reactivated", "reactivated"]
        verify = sessions()
        try:
            assert verify.scalar(select(User.is_active).where(User.id == target_id)) is True
            assert verify.scalar(select(User.is_active).where(User.id == other_id)) is True
            assert verify.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.action == "personnel.status_update",
                    AuditLog.target_id == str(target_id),
                )
            ) == 1
        finally:
            verify.close()
    finally:
        release_first_lock.set()
        event.remove(engine, "after_cursor_execute", hold_first_target_lock)
        cleanup = sessions()
        try:
            if organization_id is not None:
                cleanup.execute(
                    delete(AuditLog).where(AuditLog.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(User).where(User.organization_id == organization_id)
                )
                cleanup.execute(
                    delete(Organization).where(Organization.id == organization_id)
                )
            if actor_id is not None:
                cleanup.execute(delete(User).where(User.id == actor_id))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def test_admin_stats_returns_system_counts(
    admin_client, db_session, existing_organization, organization_user
):
    inactive = Organization(
        name="Inactive Organization",
        code="STATS-INACTIVE",
        status=RecordStatus.inactive,
    )
    db_session.add(inactive)
    db_session.flush()
    db_session.add(
        User(
            username="stats-inactive-account",
            password_hash="hash",
            role=UserRole.organization,
            organization_id=inactive.id,
            is_active=False,
        )
    )
    db_session.flush()

    response = admin_client.get("/admin/stats")

    assert response.status_code == 200
    assert response.json() == {
        "organizations": {"total": 2, "active": 1, "inactive": 1},
        "accounts": {"total": 2, "active": 1, "inactive": 1},
        "batches": {"total": 0},
        "certificates": {"total": 0},
    }


def test_audit_log_hides_legacy_organization_create_code_without_mutating_history(
    admin_client, db_session, admin_user, existing_organization
):
    legacy_code = "ORGABCDEFG123456"
    legacy_details = {
        "code": legacy_code,
        "username": "legacy-organization-user",
        "legacy_note": "preserve this",
    }
    legacy_audit = AuditLog(
        actor_id=admin_user.id,
        organization_id=existing_organization.id,
        action="organization.create",
        target_type="organization",
        target_id=str(existing_organization.id),
        details=legacy_details,
    )
    unrelated_audit = AuditLog(
        actor_id=admin_user.id,
        organization_id=existing_organization.id,
        action="certificate.create",
        target_type="certificate",
        target_id="legacy-certificate",
        details={"code": "CERTIFICATE-DETAIL", "verification_code": "VERIFY-001"},
    )
    db_session.add_all([legacy_audit, unrelated_audit])
    db_session.flush()

    response = admin_client.get("/admin/audit-logs")

    assert response.status_code == 200
    items_by_action = {item["action"]: item for item in response.json()["items"]}
    assert items_by_action["organization.create"]["details"] == {
        "username": "legacy-organization-user",
        "legacy_note": "preserve this",
    }
    assert legacy_code not in response.text
    assert items_by_action["certificate.create"]["details"] == {
        "code": "CERTIFICATE-DETAIL",
        "verification_code": "VERIFY-001",
    }
    assert db_session.get(AuditLog, legacy_audit.id).details == legacy_details


def test_admin_filters_paginated_audit_logs(
    admin_client, db_session, admin_user, existing_organization
):
    other = Organization(name="Other Organization", code="AUDIT-OTHER")
    db_session.add(other)
    db_session.flush()
    base = datetime(2026, 8, 10, tzinfo=timezone.utc)
    logs = [
        AuditLog(
            actor_id=admin_user.id,
            organization_id=existing_organization.id,
            action="organization.update",
            target_type="organization",
            target_id=str(existing_organization.id),
            details={"sequence": 1},
            created_at=base,
        ),
        AuditLog(
            actor_id=admin_user.id,
            organization_id=existing_organization.id,
            action="organization.update",
            target_type="organization",
            target_id=str(existing_organization.id),
            details={"sequence": 2},
            created_at=base + timedelta(days=1),
        ),
        AuditLog(
            actor_id=admin_user.id,
            organization_id=other.id,
            action="organization.update",
            target_type="organization",
            target_id=str(other.id),
            details={"sequence": 3},
            created_at=base + timedelta(days=1),
        ),
        AuditLog(
            actor_id=admin_user.id,
            organization_id=existing_organization.id,
            action="organization.create",
            target_type="organization",
            target_id=str(existing_organization.id),
            details={"sequence": 4},
            created_at=base + timedelta(days=1),
        ),
    ]
    db_session.add_all(logs)
    db_session.flush()

    response = admin_client.get(
        "/admin/audit-logs",
        params={
            "organization_id": existing_organization.id,
            "action": "organization.update",
            "date_from": "2026-08-10T12:00:00Z",
            "date_to": "2026-08-12T00:00:00Z",
            "page": 1,
            "page_size": 1000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 100
    assert body["items"][0]["details"] == {"sequence": 2}
    assert body["items"][0]["actor"]["username"] == admin_user.username
    assert "code" not in body["items"][0]["organization"]
    assert "EXISTING" not in response.text
    assert "password" not in response.text.lower()


def test_delete_personnel_requires_inactive_and_preserves_history(admin_client, db_session, organization_user, successor_user):
    from auth import user_has_system_access
    user = organization_user
    path = f"/admin/organizations/{user.organization_id}/users/{user.id}"
    assert admin_client.delete(path).status_code == 409
    assert admin_client.patch(path, json={"is_active": False}).status_code == 200
    assert admin_client.delete(path).status_code == 204
    db_session.refresh(user)
    assert user.deleted_at is not None
    assert not user_has_system_access(user)
    assert admin_client.delete(path).status_code == 404
    assert admin_client.patch(path, json={"is_active": True}).status_code == 404
    assert admin_client.post(path + "/reset-password", json={"password": "NewPassword123!"}).status_code == 404
    items = admin_client.get(f"/admin/organizations/{user.organization_id}/users").json()["items"]
    assert [item["id"] for item in items] == [successor_user.id]
    assert db_session.scalar(select(AuditLog.id).where(AuditLog.action == "personnel.delete", AuditLog.target_id == str(user.id)))
    assert admin_client.get("/admin/stats").json()["accounts"]["total"] == 1


def test_delete_organization_hides_children_and_cannot_restore(admin_client, db_session, organization_user):
    from auth import user_has_system_access
    user = organization_user
    org = user.organization
    path = f"/admin/organizations/{org.id}"
    assert admin_client.delete(path).status_code == 409
    assert admin_client.patch(path, json={"status": "inactive"}).status_code == 200
    assert admin_client.delete(path).status_code == 204
    db_session.refresh(org)
    db_session.refresh(user)
    assert org.deleted_at is not None and user.deleted_at is not None
    assert not user_has_system_access(user)
    assert admin_client.get("/admin/organizations").json()["total"] == 0
    assert admin_client.get(path + "/users").status_code == 404
    assert admin_client.patch(path, json={"status": "active"}).status_code == 404
    assert admin_client.post(path + "/users", json={"username": "newuser", "password": "NewPassword123!"}).status_code == 404
    stats = admin_client.get("/admin/stats").json()
    assert stats["organizations"]["total"] == stats["accounts"]["total"] == 0
    assert db_session.scalar(select(AuditLog.id).where(AuditLog.action == "organization.delete"))


def test_delete_requires_admin_and_matching_organization(client, db_session, organization_user, other_organization_user, admin_user):
    user = organization_user
    path = f"/admin/organizations/{user.organization_id}"
    for target in (path, path + f"/users/{user.id}"):
        assert client.delete(target).status_code == 401
        assert client.delete(target, headers=authorization(user)).status_code == 403
    wrong = path + f"/users/{other_organization_user.id}"
    assert client.delete(wrong, headers=authorization(admin_user)).status_code == 404


@pytest.mark.parametrize("delete_parent", [False, True])
def test_deletion_retains_certificate_and_image(admin_client, db_session, organization_user, tmp_path, delete_parent):
    from datetime import date
    from models import CertificateStatus
    from services.builtin_templates import ensure_original_template
    user = organization_user
    template = ensure_original_template(db_session, user.organization_id)
    image = tmp_path / "retained.webp"
    image.write_bytes(b"existing certificate")
    certificate = Certificate(
        organization_id=user.organization_id, template_id=template.id,
        created_by_id=user.id, verification_code="retain1234567890",
        recipient_reference="reference", recipient_name="Recipient", topic="Topic",
        issue_date=date.today(), image_path=str(image),
    )
    db_session.add(certificate)
    db_session.flush()
    path = f"/admin/organizations/{user.organization_id}"
    if delete_parent:
        assert admin_client.patch(path, json={"status": "inactive"}).status_code == 200
    else:
        user.is_active = False
        db_session.flush()
        path += f"/users/{user.id}"
    assert admin_client.delete(path).status_code == 204
    db_session.refresh(certificate)
    assert certificate.status == CertificateStatus.active
    assert certificate.created_by.id == user.id
    assert certificate.organization.id == user.organization_id
    assert image.read_bytes() == b"existing certificate"
