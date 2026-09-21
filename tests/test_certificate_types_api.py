import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import create_access_token
from core.errors import register_error_handlers
from database import get_db
from models import Organization, User, UserRole
from routers.certificate_types import router as certificate_types_router


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def org_user(db_session):
    org = Organization(name="Certificate Type Org", code="TYPE-ORG")
    user = User(username="type-user", password_hash="hash", role=UserRole.organization, organization=org)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def client(db_session):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(certificate_types_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app, raise_server_exceptions=False)


def test_certificate_type_crud(client, org_user):
    client.headers.update(auth(org_user))
    created = client.post("/certificate-types", json={"name": "ผ่านการอบรม", "description": "Training"})
    assert created.status_code == 201
    type_id = created.json()["id"]
    listed = client.get("/certificate-types")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == type_id
    updated = client.patch(f"/certificate-types/{type_id}", json={"name": "ผ่านการอบรมขั้นสูง"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "ผ่านการอบรมขั้นสูง"
