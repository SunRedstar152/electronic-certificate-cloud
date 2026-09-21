from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from auth import create_access_token
from config import settings
from core.errors import register_error_handlers
from database import get_db
from models import AuditLog, FontAsset, Organization, RecordStatus, Template, User, UserRole
from routers.fonts import router as fonts_router
from services.font_files import MAX_FONT_BYTES
from services.font_registry import BUILTIN_FONTS


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def organization(db_session):
    value = Organization(name="Font Organization", code="FONT-ORG")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization(db_session):
    value = Organization(name="Other Font Organization", code="OTHER-FONT-ORG")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, organization):
    value = User(
        username="font-organization-user",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def admin_user(db_session):
    value = User(
        username="font-admin-user",
        password_hash="hash",
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def owned_font(db_session, organization, tmp_path):
    path = tmp_path / "owned.ttf"
    path.write_bytes(BUILTIN_FONTS["sarabun"].regular_path.read_bytes())
    value = FontAsset(
        organization_id=organization.id,
        display_name="Owned Thai Font",
        original_filename="owned.ttf",
        file_path=path.as_posix(),
        sha256="a" * 64,
        format="ttf",
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_font(db_session, other_organization, tmp_path):
    path = tmp_path / "other.ttf"
    path.write_bytes(BUILTIN_FONTS["sarabun"].regular_path.read_bytes())
    value = FontAsset(
        organization_id=other_organization.id,
        display_name="Other Thai Font",
        original_filename="other.ttf",
        file_path=path.as_posix(),
        sha256="b" * 64,
        format="ttf",
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def app(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FONT_DIR", tmp_path / "font-uploads")
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(fonts_router)

    def override_get_db():
        yield db_session

    value.dependency_overrides[get_db] = override_get_db
    return value


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def organization_client(client, organization_user):
    client.headers.update(authorization(organization_user))
    return client


def test_font_list_contains_builtins_and_only_owned_uploads(
    organization_client, owned_font, other_font
):
    response = organization_client.get("/fonts")

    assert response.status_code == 200
    builtins = {
        item["key"] for item in response.json() if item["source"] == "builtin"
    }
    uploads = {
        item["id"] for item in response.json() if item["source"] == "uploaded"
    }
    assert builtins == {"sarabun", "noto_sans_thai", "th_niramit_as"}
    assert uploads == {owned_font.id}


def test_font_list_omits_inactive_uploads(organization_client, owned_font):
    owned_font.status = RecordStatus.inactive

    response = organization_client.get("/fonts")

    assert response.status_code == 200
    assert all(
        item["source"] == "builtin" or item["id"] != owned_font.id
        for item in response.json()
    )


def test_upload_stores_owned_font_and_records_audit(
    organization_client, db_session, organization, organization_user
):
    source = BUILTIN_FONTS["sarabun"].regular_path
    response = organization_client.post(
        "/fonts",
        data={"display_name": "ฟอนต์ไทยของหน่วยงาน"},
        files={"file": (source.name, source.read_bytes(), "font/ttf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "uploaded"
    assert body["display_name"] == "ฟอนต์ไทยของหน่วยงาน"
    assert body["supports_thai"] is True
    asset = db_session.get(FontAsset, body["id"])
    assert asset.organization_id == organization.id
    stored = Path(asset.file_path)
    assert stored.is_file()
    assert stored.parent == settings.FONT_DIR / str(organization.id)
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "font.create"))
    assert audit.actor_id == organization_user.id
    assert audit.target_id == str(asset.id)


def test_duplicate_upload_returns_existing_owned_asset(
    organization_client, db_session
):
    source = BUILTIN_FONTS["sarabun"].regular_path
    upload = {
        "data": {"display_name": "Sarabun copy"},
        "files": {"file": (source.name, source.read_bytes(), "font/ttf")},
    }

    first = organization_client.post("/fonts", **upload)
    second = organization_client.post("/fonts", **upload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(list(db_session.scalars(select(FontAsset)))) == 1
    assert len(list((settings.FONT_DIR / str(first.json()["organization_id"])).iterdir())) == 1


@pytest.mark.parametrize(
    ("data", "filename", "expected_code"),
    [
        (b"not-a-font", "broken.ttf", "FONT_INVALID_FILE"),
        (b"x" * (MAX_FONT_BYTES + 1), "large.ttf", "FONT_INVALID_FILE"),
    ],
    ids=["corrupt", "oversize"],
)
def test_upload_rejects_invalid_font_without_leaving_file(
    organization_client, data, filename, expected_code
):
    response = organization_client.post(
        "/fonts",
        data={"display_name": "Bad Font"},
        files={"file": (filename, data, "font/ttf")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
    assert not settings.FONT_DIR.exists()


def test_other_organization_font_is_not_disclosed_or_deactivated(
    organization_client, other_font
):
    response = organization_client.delete(f"/fonts/{other_font.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FONT_NOT_FOUND"


def test_deactivate_rejects_font_used_by_active_template(
    organization_client, db_session, organization, owned_font
):
    template = Template(
        organization_id=organization.id,
        name="Template using uploaded font",
        background_image_path="template.png",
        positions={},
        dimensions={"width": 1600, "height": 1131},
        layout={
            "version": 2,
            "elements": [
                {
                    "id": "recipient_name",
                    "source": "recipient_name",
                    "kind": "text",
                    "label": "ชื่อผู้รับ",
                    "required": True,
                    "visible": True,
                    "box": {"x": 0.2, "y": 0.4, "width": 0.6, "height": 0.1},
                    "style": {
                        "font": {"source": "uploaded", "id": owned_font.id},
                        "font_size_px": 52,
                        "color": "#111111",
                        "weight": 400,
                        "align": "center",
                        "line_height": 1.25,
                    },
                }
            ],
        },
        status=RecordStatus.active,
    )
    db_session.add(template)
    db_session.flush()

    response = organization_client.delete(f"/fonts/{owned_font.id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FONT_IN_USE"
    db_session.refresh(owned_font)
    assert owned_font.status == RecordStatus.active


def test_deactivate_marks_unused_font_inactive_and_records_audit(
    organization_client, db_session, owned_font
):
    response = organization_client.delete(f"/fonts/{owned_font.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "inactive"
    db_session.refresh(owned_font)
    assert owned_font.status == RecordStatus.inactive
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "font.deactivate")
    )
    assert audit.target_id == str(owned_font.id)


def test_admin_cannot_use_organization_font_routes(client, admin_user):
    client.headers.update(authorization(admin_user))
    response = client.get("/fonts")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"
