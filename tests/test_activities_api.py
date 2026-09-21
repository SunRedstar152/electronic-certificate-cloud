from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from auth import create_access_token
from core.errors import register_error_handlers
from database import get_db
from models import Activity, ActivityApprovalLog, Organization, User, UserRole
from routers.activities import router as activities_router


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def graph(db_session):
    org = Organization(name="Activity API Org", code="ACT-API")
    staff = User(username="activity-api-staff", password_hash="hash", role=UserRole.organization, organization=org)
    approver = User(username="activity-api-approver", password_hash="hash", role=UserRole.organization, organization=org)
    other_org = Organization(name="Other Activity Org", code="ACT-OTHER")
    other = User(username="activity-api-other", password_hash="hash", role=UserRole.organization, organization=other_org)
    db_session.add_all([org, staff, approver, other])
    db_session.flush()
    return org, staff, approver, other


@pytest.fixture
def client(db_session):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activities_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app, raise_server_exceptions=False)


def payload():
    return {"activity_name": "PKRU Workshop", "description": "Details", "activity_date": "2026-08-28", "activity_type": "อบรม"}


def test_activity_can_be_submitted_approved_and_records_history(client, db_session, graph):
    org, staff, approver, _ = graph
    client.headers.update(auth(staff))
    created = client.post("/activities", json=payload())
    assert created.status_code == 201
    activity_id = created.json()["id"]
    assert client.post(f"/activities/{activity_id}/submit").json()["approval_status"] == "PENDING_APPROVAL"

    client.headers.update(auth(approver))
    approved = client.post(f"/activities/{activity_id}/approve", json={"comment": "Approved"})
    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "APPROVED"
    assert db_session.scalar(select(ActivityApprovalLog).where(ActivityApprovalLog.activity_id == activity_id)).comment == "Approved"


def test_activity_cross_organization_access_is_denied(client, graph):
    org, staff, _, other = graph
    client.headers.update(auth(staff))
    activity = client.post("/activities", json=payload()).json()
    client.headers.update(auth(other))
    response = client.get(f"/activities/{activity['id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACTIVITY_NOT_FOUND"
