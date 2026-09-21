from datetime import date
from io import BytesIO
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from sqlalchemy import func, select

from auth import create_access_token
from config import settings
from core.errors import register_error_handlers
from database import get_db
from models import (
    AuditLog,
    Certificate,
    FontAsset,
    Organization,
    RecordStatus,
    Template,
    User,
    UserRole,
)
import routers.templates as templates_module
from routers.templates import router as templates_router
from services.template_files import (
    MAX_TEMPLATE_IMAGE_BYTES,
    TemplateImageError,
    store_template_image,
    validate_template_image,
)


VALID_POSITIONS = {
    "recipient_name": {"x": 0.5, "y": 0.45},
    "topic": {"x": 0.5, "y": 0.55},
    "issue_date": {"x": 0.25, "y": 0.8},
    "qr_code": {"x": 0.85, "y": 0.8},
}


def image_bytes(image_format="PNG", size=(8, 6)):
    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format=image_format)
    return output.getvalue()


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def organization(db_session):
    value = Organization(name="Template Organization", code="TEMPLATE-ORG")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization(db_session):
    value = Organization(name="Other Organization", code="TEMPLATE-OTHER")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, organization):
    value = User(
        username="template-organization-user",
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
        username="template-admin",
        password_hash="hash",
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def own_template(db_session, organization):
    value = Template(
        organization_id=organization.id,
        name="Own Template",
        background_image_path="own.png",
        positions=VALID_POSITIONS,
        dimensions={"width": 800, "height": 600},
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_template(db_session, other_organization):
    value = Template(
        organization_id=other_organization.id,
        name="Other Template",
        background_image_path="other.png",
        positions=VALID_POSITIONS,
        dimensions={"width": 800, "height": 600},
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def app(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMPLATE_DIR", tmp_path / "template-uploads")
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(templates_router)

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


@pytest.fixture
def admin_client(client, admin_user):
    client.headers.update(authorization(admin_user))
    return client


def create_payload(**overrides):
    value = {
        "name": "Training Certificate",
        "positions": __import__("json").dumps(VALID_POSITIONS),
    }
    value.update(overrides)
    return value


def valid_layout(*, font=None):
    return {
        "version": 2,
        "elements": [
            {
                "id": "recipient_name",
                "source": "recipient_name",
                "kind": "text",
                "label": "ชื่อผู้รับ",
                "required": True,
                "visible": True,
                "box": {"x": 0.15, "y": 0.35, "width": 0.7, "height": 0.18},
                "style": {
                    "font": font or {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 48,
                    "color": "#111111",
                    "weight": 700,
                    "align": "center",
                    "line_height": 1.25,
                },
                "sample_value": "สมชาย ใจดี",
            },
            {
                "id": "verification_qr",
                "source": "verification_url",
                "kind": "qr",
                "label": "คิวอาร์โค้ด",
                "required": True,
                "visible": True,
                "box": {"x": 0.78, "y": 0.72, "width": 0.14, "height": 0.18},
                "sample_value": "https://certificates.example/?code=PREVIEW",
            },
        ],
    }


def signature_layout():
    return {
        "version": 2,
        "elements": [{
            "id": "signature", "source": "signature", "kind": "signature",
            "label": "ลายเซ็นผู้ลงนาม", "required": True, "visible": True,
            "box": {"x": 0.35, "y": 0.66, "width": 0.30, "height": 0.24},
            "style": {
                "font": {"source": "builtin", "key": "sarabun"},
                "font_size_px": 28, "color": "#111111", "weight": 400,
                "align": "center", "line_height": 1.2,
            },
        }],
    }


def test_create_accepts_and_persists_layout_v2_without_legacy_positions(
    organization_client, db_session
):
    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data={
            "name": "Dynamic Training Certificate",
            "layout": json.dumps(valid_layout(), ensure_ascii=False),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["positions"] == {}
    assert body["layout"]["version"] == 2
    assert body["layout"]["elements"][0]["label"] == "ชื่อผู้รับ"
    stored = db_session.get(Template, body["id"])
    assert stored.layout == body["layout"]


def test_create_accepts_layout_with_tiny_qr(organization_client):
    layout = valid_layout()
    qr = next(element for element in layout["elements"] if element["kind"] == "qr")
    qr["box"] = {"x": 0.1, "y": 0.1, "width": 0.01, "height": 0.01}

    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data={
            "name": "Tiny QR Template",
            "layout": json.dumps(layout, ensure_ascii=False),
        },
    )

    assert response.status_code == 201, response.text
    stored_qr = next(item for item in response.json()["layout"]["elements"] if item["id"] == qr["id"])
    assert stored_qr["box"] == qr["box"]


def test_create_accepts_qr_on_tiny_canvas(
    organization_client,
):
    layout = valid_layout()

    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(32, 32)), "image/png")},
        data={
            "name": "Tiny Canvas Template",
            "layout": json.dumps(layout, ensure_ascii=False),
        },
    )

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("side", [64, 77, 78])
def test_create_and_preview_scale_qr_caption_space(organization_client, side):
    layout = valid_layout()
    qr = next(element for element in layout["elements"] if element["kind"] == "qr")
    qr["box"] = {"x": 0, "y": 0, "width": side / 400, "height": side / 400}
    layout["elements"] = [qr]
    for endpoint, fields in (
        ("/templates", {"name": "QR with ID"}),
        ("/templates/preview", {"sample_values": "{}"}),
    ):
        response = organization_client.post(
            endpoint,
            files={"file": ("qr.png", image_bytes(size=(400, 400)), "image/png")},
            data={"layout": json.dumps(layout), **fields},
        )
        success_status = 201 if endpoint == "/templates" else 200
        assert response.status_code == success_status, response.text


def test_update_accepts_layout_v2_and_records_layout_field(
    organization_client, db_session, own_template
):
    response = organization_client.patch(
        f"/templates/{own_template.id}",
        json={"layout": valid_layout()},
    )

    assert response.status_code == 200
    assert response.json()["layout"]["version"] == 2
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "template.update")
    )
    assert audit.details["fields"] == ["layout"]


def test_update_rejects_changing_source_for_existing_element(
    organization_client, db_session, own_template
):
    original = valid_layout()
    original["elements"].insert(
        1,
        {
            **original["elements"][0],
            "id": "department",
            "source": "custom.department",
            "label": "หน่วยงาน",
        },
    )
    own_template.layout = original
    db_session.flush()
    renamed = json.loads(json.dumps(original))
    renamed["elements"][1]["source"] = "custom.office"

    response = organization_client.patch(
        f"/templates/{own_template.id}", json={"layout": renamed}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TEMPLATE_FIELD_SOURCE_IMMUTABLE"


def test_owned_background_is_served_through_authenticated_endpoint(
    organization_client, own_template, tmp_path
):
    background = tmp_path / "template-uploads" / "owned-background.png"
    background.parent.mkdir(parents=True, exist_ok=True)
    background.write_bytes(image_bytes())
    own_template.background_image_path = background.as_posix()

    response = organization_client.get(f"/templates/{own_template.id}/background")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"


def test_background_endpoint_does_not_cross_organization_scope(
    organization_client, other_template
):
    response = organization_client.get(
        f"/templates/{other_template.id}/background"
    )

    assert response.status_code == 404


def test_background_endpoint_rejects_path_outside_configured_storage(
    organization_client, own_template, tmp_path
):
    outside = tmp_path / "outside.png"
    outside.write_bytes(image_bytes())
    own_template.background_image_path = outside.as_posix()

    response = organization_client.get(f"/templates/{own_template.id}/background")

    assert response.status_code == 404


def test_preview_returns_real_png_without_creating_certificate(
    organization_client, db_session, own_template, tmp_path
):
    background = settings.TEMPLATE_DIR / "preview-background.png"
    background.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), "white").save(background)
    own_template.background_image_path = background.as_posix()
    own_template.dimensions = {"width": 800, "height": 600}
    db_session.flush()
    before = db_session.scalar(select(func.count(Certificate.id)))

    response = organization_client.post(
        "/templates/preview",
        data={
            "template_id": str(own_template.id),
            "layout": json.dumps(valid_layout(), ensure_ascii=False),
            "sample_values": json.dumps(
                {
                    "recipient_name": "สมชาย ใจดี",
                    "verification_url": "https://certificates.example/?code=PREVIEW",
                },
                ensure_ascii=False,
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\x89PNG")
    assert Image.open(BytesIO(response.content)).size == (800, 600)
    assert db_session.scalar(select(func.count(Certificate.id))) == before


def test_preview_renders_signature_with_synthetic_values_without_storing_file(
    organization_client,
):
    response = organization_client.post(
        "/templates/preview",
        files={"file": ("certificate.png", image_bytes(size=(1000, 1000)), "image/png")},
        data={
            "layout": json.dumps(signature_layout(), ensure_ascii=False),
            "sample_values": "{}",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    with Image.open(BytesIO(response.content)) as rendered:
        assert ImageChops.difference(
            Image.new("RGB", rendered.size, "white"), rendered.convert("RGB")
        ).getbbox()
    assert not settings.TEMPLATE_DIR.exists()


def test_preview_renders_every_signature_block(organization_client):
    from copy import deepcopy

    layout = signature_layout()
    base = layout["elements"][0]
    layout["elements"] = []
    for index in range(3):
        element = deepcopy(base)
        element["id"] = f"signature_{index}"
        element["box"] = {"x": 0.02 + index * 0.33, "y": 0.1, "width": 0.30, "height": 0.7}
        layout["elements"].append(element)
    response = organization_client.post(
        "/templates/preview",
        files={"file": ("certificate.png", image_bytes(size=(1000, 1000)), "image/png")},
        data={"layout": json.dumps(layout, ensure_ascii=False), "sample_values": "{}"},
    )
    assert response.status_code == 200, response.text
    with Image.open(BytesIO(response.content)) as rendered:
        for left in (20, 350, 680):
            region = rendered.convert("RGB").crop((left, 100, left + 300, 800))
            assert ImageChops.difference(Image.new("RGB", region.size, "white"), region).getbbox()


def test_preview_renders_small_qr(
    organization_client, own_template
):
    background = settings.TEMPLATE_DIR / "small-qr-preview.png"
    background.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), "white").save(background)
    own_template.background_image_path = background.as_posix()
    own_template.dimensions = {"width": 800, "height": 600}
    layout = valid_layout()
    qr = next(element for element in layout["elements"] if element["kind"] == "qr")
    qr["box"] = {"x": 0.1, "y": 0.1, "width": 0.01, "height": 0.01}

    response = organization_client.post(
        "/templates/preview",
        data={
            "template_id": str(own_template.id),
            "layout": json.dumps(layout, ensure_ascii=False),
            "sample_values": json.dumps(
                {"verification_url": "https://example.invalid/?code=TINY"}
            ),
        },
    )

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\x89PNG")


def test_preview_new_image_does_not_store_template_or_upload(
    organization_client, db_session
):
    before = db_session.scalar(select(func.count(Template.id)))
    response = organization_client.post(
        "/templates/preview",
        data={
            "layout": json.dumps(valid_layout(), ensure_ascii=False),
            "sample_values": json.dumps(
                {
                    "recipient_name": "ผู้เข้าร่วมอบรม",
                    "verification_url": "https://certificates.example/?code=PREVIEW",
                },
                ensure_ascii=False,
            ),
        },
        files={"file": ("preview.png", image_bytes(size=(800, 600)), "image/png")},
    )

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert db_session.scalar(select(func.count(Template.id))) == before
    assert not settings.TEMPLATE_DIR.exists()


def test_create_rejects_uploaded_font_owned_by_another_organization(
    organization_client,
    db_session,
    other_organization,
):
    font = FontAsset(
        organization_id=other_organization.id,
        display_name="Other Font",
        original_filename="other.ttf",
        file_path="other.ttf",
        sha256="e" * 64,
        format="ttf",
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(font)
    db_session.flush()

    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data={
            "name": "Unsafe Cross-Organization Font",
            "layout": json.dumps(
                valid_layout(font={"source": "uploaded", "id": font.id}),
                ensure_ascii=False,
            ),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_FONT_NOT_FOUND"
    assert not settings.TEMPLATE_DIR.exists()


def test_rejects_fake_png(organization_client):
    response = organization_client.post(
        "/templates",
        files={"file": ("fake.png", b"not an image", "image/png")},
        data=create_payload(name="Bad"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_INVALID_IMAGE"


def test_other_organization_template_is_not_disclosed(
    organization_client, other_template
):
    response = organization_client.get(f"/templates/{other_template.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_validate_template_image_decodes_png_and_jpeg():
    png = validate_template_image(image_bytes("PNG"), "template.png")
    jpeg = validate_template_image(image_bytes("JPEG"), "template.jpg")

    assert (png.extension, png.width, png.height) == (".webp", 8, 6)
    assert (jpeg.extension, jpeg.width, jpeg.height) == (".webp", 8, 6)


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (image_bytes("PNG"), "template.jpg"),
        (image_bytes("JPEG"), "template.png"),
        (image_bytes("GIF"), "template.gif"),
        (b"x" * (MAX_TEMPLATE_IMAGE_BYTES + 1), "template.png"),
    ],
    ids=["png-as-jpeg", "jpeg-as-png", "gif", "oversize"],
)
def test_validate_template_image_rejects_mismatch_unsupported_and_oversize(
    data, filename
):
    with pytest.raises(TemplateImageError):
        validate_template_image(data, filename)


def test_validate_template_image_rejects_decompression_bomb(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)

    with pytest.raises(TemplateImageError):
        validate_template_image(image_bytes("PNG", (5, 5)), "template.png")


def test_store_template_image_uses_uuid_name_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "TEMPLATE_DIR", tmp_path)
    validated = validate_template_image(image_bytes(), "original.png")

    stored = store_template_image(validated)

    assert stored.parent == tmp_path
    assert stored.suffix == ".webp"
    UUID(stored.stem)
    assert stored.read_bytes() == validated.data
    assert list(tmp_path.iterdir()) == [stored]


def test_upload_read_is_bounded_before_validation():
    class TrackingStream(BytesIO):
        requested_size = None

        def read(self, size=-1):
            self.requested_size = size
            return super().read(size)

    stream = TrackingStream(b"small")
    upload = UploadFile(filename="template.png", file=stream)

    assert templates_module._read_upload_data(upload) == b"small"
    assert stream.requested_size == MAX_TEMPLATE_IMAGE_BYTES + 1


def test_create_derives_organization_stores_dimensions_and_records_audit(
    organization_client, db_session, organization, organization_user
):
    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data=create_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["organization_id"] == organization.id
    assert body["positions"] == VALID_POSITIONS
    assert body["dimensions"] == {"width": 800, "height": 600}
    stored_path = Path(body["background_image_path"])
    assert stored_path.exists()
    assert stored_path.parent == settings.TEMPLATE_DIR

    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "template.create")
    )
    assert audit.actor_id == organization_user.id
    assert audit.organization_id == organization.id
    assert audit.target_id == str(body["id"])


@pytest.mark.parametrize(
    "positions",
    [
        {"unsupported": {"x": 0.5, "y": 0.5}},
        {"recipient_name": {"x": -0.01, "y": 0.5}},
        {"recipient_name": {"x": 0.5, "y": 1.01}},
        {"recipient_name": {"x": "0.5", "y": 0.5}},
        {"recipient_name": {"x": 0.5}},
        {"recipient_name": {"x": 0.5, "y": 0.5, "size": 12}},
    ],
)
def test_create_rejects_invalid_positions_without_storing_file(
    organization_client, db_session, positions
):
    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data=create_payload(positions=__import__("json").dumps(positions)),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_INVALID_POSITIONS"
    assert db_session.scalar(select(func.count()).select_from(Template)) == 0
    assert not settings.TEMPLATE_DIR.exists()


def test_create_rejects_invalid_positions_json(organization_client):
    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data=create_payload(positions="not-json"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_INVALID_POSITIONS"


def test_create_rejects_whitespace_only_name_without_storing_file(
    organization_client, db_session
):
    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(), "image/png")},
        data=create_payload(name="   "),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert db_session.scalar(select(func.count()).select_from(Template)) == 0
    assert not settings.TEMPLATE_DIR.exists()


def test_list_returns_only_organization_templates(
    organization_client, own_template, other_template
):
    response = organization_client.get("/templates")

    assert response.status_code == 200
    assert [item["id"] for item in response.json() if not item["is_builtin"]] == [own_template.id]
    assert len([item for item in response.json() if item["is_builtin"]]) == 1


def test_admin_cannot_use_organization_template_routes(admin_client):
    response = admin_client.get("/templates")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_update_changes_owned_template_and_records_audit(
    organization_client, db_session, own_template
):
    response = organization_client.patch(
        f"/templates/{own_template.id}",
        json={
            "name": "Updated Template",
            "positions": {"recipient_name": {"x": 0.1, "y": 0.2}},
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated Template"
    assert response.json()["positions"] == {
        "recipient_name": {"x": 0.1, "y": 0.2}
    }
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "template.update")
    )
    assert audit.target_id == str(own_template.id)


def test_update_does_not_disclose_other_organization_template(
    organization_client, other_template
):
    response = organization_client.patch(
        f"/templates/{other_template.id}", json={"name": "Stolen"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


@pytest.mark.parametrize(
    "change",
    [
        {"dimensions": {"width": 999, "height": 999}},
        {"status": "inactive"},
    ],
)
def test_update_rejects_derived_or_lifecycle_fields(
    organization_client, own_template, change
):
    response = organization_client.patch(
        f"/templates/{own_template.id}", json=change
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_INVALID_UPDATE"


def test_deactivate_preserves_template_and_image_and_records_audit(
    organization_client,
    db_session,
    organization_user,
    own_template,
    tmp_path,
):
    image_path = tmp_path / "existing.png"
    image_path.write_bytes(image_bytes())
    own_template.background_image_path = str(image_path)
    db_session.flush()
    certificate = Certificate(
        organization_id=own_template.organization_id,
        template_id=own_template.id,
        verification_code="template-deactivation-preserves-certificate",
        recipient_reference="RECIPIENT-001",
        recipient_name="Existing Recipient",
        topic="Existing Topic",
        issue_date=date(2026, 8, 12),
        created_by_id=organization_user.id,
    )
    db_session.add(certificate)
    db_session.flush()

    response = organization_client.post(f"/templates/{own_template.id}/deactivate")

    assert response.status_code == 200
    assert response.json()["status"] == "inactive"
    db_session.refresh(own_template)
    assert own_template.status == RecordStatus.inactive
    assert image_path.exists()
    assert db_session.get(Certificate, certificate.id) is certificate
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "template.deactivate")
    )
    assert audit.target_id == str(own_template.id)


def test_database_failure_removes_newly_stored_image(
    organization_client, db_session, monkeypatch
):
    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    monkeypatch.setattr(db_session, "rollback", lambda: None)

    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data=create_payload(),
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert settings.TEMPLATE_DIR.exists()
    assert list(settings.TEMPLATE_DIR.iterdir()) == []


def test_rollback_failure_still_removes_newly_stored_image(
    organization_client, db_session, monkeypatch
):
    def fail_commit():
        raise RuntimeError("database unavailable")

    def fail_rollback():
        raise RuntimeError("connection lost")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    response = organization_client.post(
        "/templates",
        files={"file": ("certificate.png", image_bytes(size=(800, 600)), "image/png")},
        data=create_payload(),
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert settings.TEMPLATE_DIR.exists()
    assert list(settings.TEMPLATE_DIR.iterdir()) == []


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "P"])
def test_upload_conversion_preserves_pixels_and_transparency(mode):
    source = Image.new("RGBA", (12, 8), (17, 45, 123, 0))
    source.putpixel((2, 3), (99, 123, 45, 128))
    source.putpixel((3, 3), (200, 10, 40, 255))
    if mode == "RGB":
        source = source.convert("RGB")
    elif mode == "P":
        source = source.quantize()
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    uploaded = buffer.getvalue()
    converted = validate_template_image(uploaded, "input.png")
    assert converted.extension == ".webp"
    with Image.open(BytesIO(uploaded)) as expected, Image.open(BytesIO(converted.data)) as actual:
        assert actual.format == "WEBP"
        assert actual.size == expected.size
        assert actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()


def test_upload_rejects_webp_dimension_overflow():
    with pytest.raises(TemplateImageError, match="16383"):
        validate_template_image(image_bytes("PNG", (16384, 2)), "too-wide.png")


def test_uploaded_png_background_is_served_as_webp(organization_client):
    response = organization_client.post(
        "/templates", files={"file": ("input.png", image_bytes(size=(800,600)), "image/png")},
        data=create_payload())
    assert response.status_code == 201
    result = organization_client.get(f"/templates/{response.json()['id']}/background")
    assert result.status_code == 200
    assert result.headers["content-type"] == "image/webp"
    with Image.open(BytesIO(result.content)) as image:
        assert image.format == "WEBP" and image.size == (800,600)
