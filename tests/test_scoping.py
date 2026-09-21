import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import create_access_token
from core.errors import register_error_handlers
from database import get_db
from models import AuditLog, Organization, Template, User, UserRole
from repositories.scoping import owned_query
from routers.templates import router as templates_router
from services.audit import record_audit


def make_template(organization, name):
    return Template(
        organization=organization,
        name=name,
        background_image_path=f"{name}.png",
        positions={},
    )


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def scoping_client(db_session):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(templates_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def organization_users(db_session):
    organization = Organization(name="Shared Organization", code="SCOPE-SHARED")
    other_organization = Organization(
        name="Isolated Organization", code="SCOPE-ISOLATED"
    )
    predecessor = User(
        username="scope-predecessor",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
        is_active=True,
    )
    successor = User(
        username="scope-successor",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
        is_active=True,
    )
    outsider = User(
        username="scope-outsider",
        password_hash="hash",
        role=UserRole.organization,
        organization=other_organization,
        is_active=True,
    )
    template = make_template(organization, "shared-template")
    db_session.add_all(
        [organization, other_organization, predecessor, successor, outsider, template]
    )
    db_session.flush()
    return predecessor, successor, outsider, template


def test_personnel_in_same_organization_share_templates_but_other_org_is_denied(
    scoping_client, organization_users
):
    predecessor, successor, outsider, template = organization_users

    predecessor_response = scoping_client.get(
        f"/templates/{template.id}", headers=authorization(predecessor)
    )
    successor_response = scoping_client.get(
        f"/templates/{template.id}", headers=authorization(successor)
    )
    outsider_response = scoping_client.get(
        f"/templates/{template.id}", headers=authorization(outsider)
    )

    assert predecessor_response.status_code == 200
    assert successor_response.status_code == 200
    assert outsider_response.status_code == 404


def test_organization_user_cannot_scope_another_organization(db_session):
    organization = Organization(name="Organization A", code="SCOPE-A")
    other_organization = Organization(name="Organization B", code="SCOPE-B")
    organization_user = User(
        username="scope-organization-user",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
    )
    own_template = make_template(organization, "own")
    other_template = make_template(other_organization, "other")
    db_session.add_all(
        [
            organization,
            other_organization,
            organization_user,
            own_template,
            other_template,
        ]
    )
    db_session.flush()

    stmt = owned_query(
        Template, current_user=organization_user, db=db_session
    )
    rows = list(db_session.scalars(stmt))

    assert [row.id for row in rows] == [own_template.id]


def test_admin_owned_query_can_read_all_organizations(db_session):
    organization_a = Organization(name="Organization C", code="SCOPE-C")
    organization_b = Organization(name="Organization D", code="SCOPE-D")
    admin = User(
        username="scope-admin-user",
        password_hash="hash",
        role=UserRole.admin,
        organization_id=None,
    )
    template_a = make_template(organization_a, "admin-a")
    template_b = make_template(organization_b, "admin-b")
    db_session.add_all(
        [organization_a, organization_b, admin, template_a, template_b]
    )
    db_session.flush()

    rows = list(db_session.scalars(owned_query(Template, admin, db_session)))

    assert {row.id for row in rows} == {template_a.id, template_b.id}


def test_record_audit_uses_actor_scope_and_scrubs_nested_secrets(db_session):
    organization = Organization(name="Organization E", code="SCOPE-E")
    actor = User(
        username="scope-audit-user",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
    )
    db_session.add_all([organization, actor])
    db_session.flush()

    audit = record_audit(
        db_session,
        actor,
        "certificate.create",
        "certificate",
        UUID("12345678-1234-5678-1234-567812345678"),
        {
            "password": "do-not-log",
            "nested": {"TOKEN": "do-not-log", "safe": "visible"},
            "items": [{"secret": "do-not-log"}],
            "occurred_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
        },
    )
    db_session.flush()

    stored = db_session.get(AuditLog, audit.id)
    assert stored.actor_id == actor.id
    assert stored.organization_id == organization.id
    assert stored.target_id == "12345678-1234-5678-1234-567812345678"
    json.dumps(stored.details)
    assert stored.details["password"] == "[REDACTED]"
    assert stored.details["nested"] == {
        "TOKEN": "[REDACTED]",
        "safe": "visible",
    }
    assert stored.details["items"] == [{"secret": "[REDACTED]"}]
    assert stored.details["occurred_at"].startswith("2026-08-12T00:00:00")
