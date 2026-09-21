from datetime import date
import base64
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from auth import create_access_token
from config import settings
from core.errors import register_error_handlers
from database import get_db
from models import (
    AuditLog,
    Certificate,
    CertificateStatus,
    Organization,
    RecordStatus,
    Template,
    User,
    UserRole,
)
from routers.certificates import router as certificates_router
import services.storage_paths as storage_paths_module


POSITIONS = {
    "name": {"x": 0.1, "y": 0.2},
    "topic": {"x": 0.1, "y": 0.4},
    "issue_date": {"x": 0.1, "y": 0.6},
    "qr": {"x": 0.7, "y": 0.2},
}


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def organization(db_session):
    value = Organization(name="Certificate API Organization", code="CERT-API")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization(db_session):
    value = Organization(name="Other API Organization", code="CERT-API-OTHER")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, organization):
    value = User(
        username="certificate-api-user",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_user(db_session, other_organization):
    value = User(
        username="certificate-api-other-user",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=other_organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def admin_user(db_session):
    value = User(
        username="certificate-api-admin",
        password_hash="hash",
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def template(db_session, organization, tmp_path):
    background = tmp_path / "api-background.png"
    Image.new("RGB", (600, 400), "white").save(background)
    value = Template(
        organization_id=organization.id,
        name="API Template",
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 600, "height": 400},
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_template(db_session, other_organization, tmp_path):
    background = tmp_path / "other-api-background.png"
    Image.new("RGB", (600, 400), "white").save(background)
    value = Template(
        organization_id=other_organization.id,
        name="Other API Template",
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 600, "height": 400},
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture(autouse=True)
def certificate_directory(tmp_path, monkeypatch):
    destination = tmp_path / "api-certificates"
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", destination)
    return destination


@pytest.fixture
def app(db_session):
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(certificates_router)

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


def certificate_payload(template, **overrides):
    value = {
        "template_id": template.id,
        "recipient_reference": "API-RECIPIENT-001",
        "recipient_name": "Grace Hopper",
        "topic": "Reliable Certificate APIs",
        "issue_date": "2026-08-12",
        "custom_fields": {"course": "PKRU"},
    }
    value.update(overrides)
    return value


def dynamic_layout(*, department_required=False):
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
                "box": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.15},
                "style": {
                    "font": {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 24,
                    "color": "#111111",
                    "weight": 400,
                    "align": "center",
                    "line_height": 1.25,
                },
            },
            {
                "id": "department",
                "source": "custom.department",
                "kind": "text",
                "label": "หน่วยงาน",
                "required": department_required,
                "visible": True,
                "box": {"x": 0.1, "y": 0.4, "width": 0.8, "height": 0.15},
                "style": {
                    "font": {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 20,
                    "color": "#111111",
                    "weight": 400,
                    "align": "center",
                    "line_height": 1.25,
                },
            },
            {
                "id": "training_period",
                "source": "custom.training_period",
                "kind": "date_range",
                "label": "ระยะเวลาอบรม",
                "required": False,
                "visible": True,
                "box": {"x": 0.1, "y": 0.6, "width": 0.8, "height": 0.15},
                "style": {
                    "font": {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 18,
                    "color": "#111111",
                    "weight": 400,
                    "align": "center",
                    "line_height": 1.25,
                },
                "format": {
                    "locale": "th-TH",
                    "calendar": "buddhist",
                    "pattern": "long",
                    "prefix": "ระหว่างวันที่ ",
                    "separator": " ถึง ",
                    "suffix": "",
                },
            },
        ],
    }


def signature_layout(*, height=0.7):
    return {
        "version": 2,
        "elements": [
            {
                "id": "signature",
                "source": "signature",
                "kind": "signature",
                "label": "ลายเซ็น",
                "required": True,
                "visible": True,
                "box": {"x": 0.2, "y": 0.2, "width": 0.6, "height": height},
                "style": {
                    "font": {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 24,
                    "color": "#111111",
                    "weight": 400,
                    "align": "center",
                    "line_height": 1.25,
                },
            }
        ],
    }


def signature_data_url():
    image = Image.new("RGBA", (180, 70), (0, 0, 0, 0))
    try:
        image.putpixel((30, 35), (20, 20, 20, 255))
        image.putpixel((90, 30), (20, 20, 20, 180))
        output = BytesIO()
        image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    finally:
        image.close()


def signature_payload(**overrides):
    value = {
        "png_data_url": signature_data_url(),
        "signer_name": "ผู้ลงนาม",
        "signer_position": "คณบดี",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("complete", [False, True])
def test_three_signatures_are_required_and_rendered_by_element_id(
    organization_client, db_session, template, complete,
):
    from copy import deepcopy

    layout = signature_layout()
    base = layout["elements"][0]
    layout["elements"] = []
    signatures = {}
    colors = [(210, 20, 20), (20, 180, 20), (20, 20, 210)]
    for index, color in enumerate(colors):
        element = deepcopy(base)
        element["id"] = f"signature_{index}"
        element["box"] = {"x": 0.02 + index * 0.33, "y": 0.1, "width": 0.30, "height": 0.7}
        layout["elements"].append(element)
        with Image.new("RGBA", (20, 10), (0, 0, 0, 0)) as image:
            image.paste((*color, 255), (2, 2, 18, 8))
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        signatures[element["id"]] = signature_payload(
            png_data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
            signer_name=f"Signer {index}",
        )
    template.layout = layout
    template.positions = {}
    db_session.flush()

    response = organization_client.post("/certificates", json=certificate_payload(
        template, custom_fields={}, signatures=(dict(reversed(list(signatures.items())))
            if complete else {"signature_0": signatures["signature_0"]}),
    ))
    if not complete:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SIGNATURE_REQUIRED"
        assert db_session.scalar(select(func.count(Certificate.id))) == 0
        return
    assert response.status_code == 201, response.text
    certificate = db_session.get(Certificate, response.json()["id"])
    with Image.open(certificate.image_path) as rendered:
        for index, color in enumerate(colors):
            x = round((0.17 + index * 0.33) * rendered.width)
            y = round(0.31 * rendered.height)
            assert rendered.convert("RGB").getpixel((x, y)) == color
    assert "signatures" not in response.json()
    assert certificate.custom_fields == {}


def test_single_issue_generates_internal_recipient_reference(
    organization_client, template
):
    payload = certificate_payload(template)
    payload.pop("recipient_reference")

    response = organization_client.post(
        "/certificates",
        json=payload,
        headers={"Idempotency-Key": "auto-reference"},
    )

    assert response.status_code == 201
    assert UUID(response.json()["recipient_reference"]).version == 4


def test_internal_recipient_reference_is_stable_on_idempotent_replay(
    organization_client, template
):
    payload = certificate_payload(template)
    payload.pop("recipient_reference")
    headers = {"Idempotency-Key": "stable-auto-reference"}

    first = organization_client.post("/certificates", json=payload, headers=headers)
    second = organization_client.post("/certificates", json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert second.json()["recipient_reference"] == first.json()["recipient_reference"]


def test_optional_blank_custom_field_is_removed(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = dynamic_layout()
    db_session.flush()
    payload = certificate_payload(
        template,
        custom_fields={"department": "   "},
    )
    payload.pop("recipient_reference")

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 201
    assert response.json()["custom_fields"] == {}


def test_required_custom_field_has_stable_error(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = dynamic_layout(department_required=True)
    db_session.flush()
    payload = certificate_payload(template, custom_fields={})
    payload.pop("recipient_reference")

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CERTIFICATE_REQUIRED_FIELD_MISSING"


@pytest.mark.parametrize(
    "custom_fields",
    [
        {"unknown": "value"},
        {
            "training_period": {
                "start": "2026-08-15",
                "end": "2026-08-13",
            }
        },
    ],
    ids=["unknown", "reversed-range"],
)
def test_invalid_custom_field_has_stable_error(
    organization_client, db_session, template, custom_fields
):
    template.positions = {}
    template.layout = dynamic_layout()
    db_session.flush()
    payload = certificate_payload(template, custom_fields=custom_fields)
    payload.pop("recipient_reference")

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CERTIFICATE_FIELD_INVALID"


def stored_certificate(db_session, organization, user, template, code, image_path):
    value = Certificate(
        organization_id=organization.id,
        template_id=template.id,
        verification_code=code,
        recipient_reference=f"REFERENCE-{code}",
        recipient_name=f"Recipient {code}",
        topic="Existing Topic",
        issue_date=date(2026, 8, 12),
        image_path=str(image_path),
        created_by_id=user.id,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def own_certificate(
    db_session, organization, organization_user, template, certificate_directory
):
    certificate_directory.mkdir(parents=True, exist_ok=True)
    image = certificate_directory / "OWNCERTIFICATE.png"
    Image.new("RGB", (20, 20), "white").save(image)
    return stored_certificate(
        db_session,
        organization,
        organization_user,
        template,
        "OWNCERTIFICATE",
        image,
    )


@pytest.fixture
def other_certificate(
    db_session,
    other_organization,
    other_user,
    other_template,
    certificate_directory,
):
    certificate_directory.mkdir(parents=True, exist_ok=True)
    image = certificate_directory / "OTHERCERTIFICATE.png"
    Image.new("RGB", (20, 20), "black").save(image)
    return stored_certificate(
        db_session,
        other_organization,
        other_user,
        other_template,
        "OTHERCERTIFICATE",
        image,
    )


def test_create_certificate_uses_idempotency_header_and_owned_template(
    organization_client, db_session, organization_user, template
):
    headers = {"Idempotency-Key": "api-create-once"}

    first = organization_client.post(
        "/certificates", json=certificate_payload(template), headers=headers
    )
    second = organization_client.post(
        "/certificates",
        json=certificate_payload(template, recipient_name="Changed"),
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["recipient_name"] == "Grace Hopper"
    assert first.json()["organization_id"] == organization_user.organization_id
    assert db_session.scalar(select(func.count(Certificate.id))) == 1


def test_create_rejects_other_organization_template(
    organization_client, other_template
):
    response = organization_client.post(
        "/certificates",
        json=certificate_payload(other_template),
        headers={"Idempotency-Key": "foreign-template"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_TEMPLATE_NOT_FOUND"


def test_list_returns_only_organization_certificates(
    organization_client, own_certificate, other_certificate
):
    response = organization_client.get("/certificates")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [own_certificate.id]


def test_other_organization_cannot_download(
    organization_client, other_certificate
):
    response = organization_client.get(
        f"/certificates/{other_certificate.verification_code}/download"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_owned_certificate_can_be_downloaded(
    organization_client, own_certificate
):
    response = organization_client.get(
        f"/certificates/{own_certificate.verification_code}/download"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "OWNCERTIFICATE.png" in response.headers["content-disposition"]


def test_owned_legacy_certificate_can_be_downloaded(
    organization_client, own_certificate, tmp_path, monkeypatch
):
    process_directory = tmp_path / "process-directory"
    process_directory.mkdir()
    monkeypatch.chdir(process_directory)
    application_root = tmp_path / "application"
    stored_path = Path("static/generated/test/certificates/legacy.png")
    legacy_image = application_root / stored_path
    legacy_image.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(legacy_image)
    monkeypatch.setattr(
        storage_paths_module,
        "APPLICATION_ROOT",
        application_root,
    )
    own_certificate.image_path = stored_path.as_posix()

    response = organization_client.get(
        f"/certificates/{own_certificate.verification_code}/download"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_owned_certificate_download_rejects_an_unmanaged_path(
    organization_client, own_certificate, tmp_path
):
    outside_image = tmp_path / "outside.png"
    Image.new("RGB", (20, 20), "black").save(outside_image)
    own_certificate.image_path = str(outside_image)

    response = organization_client.get(
        f"/certificates/{own_certificate.verification_code}/download"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_legacy_template_with_tiny_qr_can_issue_certificate(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = {
        "version": 2,
        "elements": [
            {
                "id": "legacy_tiny_qr",
                "source": "verification_url",
                "kind": "qr",
                "label": "QR",
                "required": True,
                "visible": True,
                "box": {"x": 0.1, "y": 0.1, "width": 0.01, "height": 0.01},
            }
        ],
    }
    db_session.flush()
    payload = certificate_payload(template, custom_fields={})
    payload.pop("recipient_reference")

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 201, response.text
    assert response.json()["verification_code"]


def test_positions_legacy_template_with_tiny_rendered_qr_can_issue_certificate(
    organization_client, db_session, template
):
    template.positions = {"qr_code": {"x": 0.5, "y": 0.5}}
    template.dimensions = {"width": 600, "height": 100}
    template.layout = {}
    db_session.flush()
    payload = certificate_payload(template, custom_fields={})
    payload.pop("recipient_reference")

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 201, response.text
    assert response.json()["verification_code"]


def test_other_organization_cannot_revoke(
    organization_client, other_certificate
):
    response = organization_client.post(
        f"/certificates/{other_certificate.verification_code}/revoke",
        json={"reason": "Invalid recipient"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_revoke_requires_nonempty_reason(
    organization_client, own_certificate
):
    response = organization_client.post(
        f"/certificates/{own_certificate.verification_code}/revoke",
        json={"reason": "   "},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_revoke_accepts_thai_reason(
    organization_client, own_certificate
):
    response = organization_client.post(
        f"/certificates/{own_certificate.verification_code}/revoke",
        json={"reason": "ออกเกียรติบัตรให้ผู้รับผิดคน"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == CertificateStatus.revoked.value
    assert response.json()["revocation_reason"] == "ออกเกียรติบัตรให้ผู้รับผิดคน"


def test_second_revoke_is_idempotent_and_does_not_rewrite_history(
    organization_client, db_session, own_certificate
):
    first = organization_client.post(
        f"/certificates/{own_certificate.verification_code}/revoke",
        json={"reason": "Invalid recipient"},
    )
    second = organization_client.post(
        f"/certificates/{own_certificate.verification_code}/revoke",
        json={"reason": "A later reason"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == CertificateStatus.revoked.value
    assert second.json()["revocation_reason"] == "Invalid recipient"
    assert second.json()["revoked_at"] == first.json()["revoked_at"]
    assert db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "certificate.revoke"
        )
    ) == 1
    assert Path(own_certificate.image_path).exists()


def test_admin_cannot_use_organization_certificate_routes(admin_client):
    response = admin_client.get("/certificates")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_signature_is_required_for_signature_template(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()

    response = organization_client.post(
        "/certificates", json=certificate_payload(template)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNATURE_REQUIRED"
    assert db_session.scalar(select(func.count(Certificate.id))) == 0


def test_signature_is_embedded_but_not_persisted(
    organization_client, db_session, template, certificate_directory
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    payload = certificate_payload(
        template, custom_fields={}, signature=signature_payload()
    )

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert "signature" not in body
    certificate = db_session.scalar(
        select(Certificate).where(Certificate.id == body["id"])
    )
    assert certificate is not None
    assert not hasattr(certificate, "signature")
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.target_id == str(certificate.id))
    )
    assert audit is not None
    assert "data:image/png" not in str(audit.details)
    output = Path(certificate.image_path)
    assert output.parent == certificate_directory
    assert output.exists()


def test_signature_is_rejected_for_template_without_signature_block(
    organization_client, template
):
    response = organization_client.post(
        "/certificates",
        json=certificate_payload(
            template, custom_fields={}, signature=signature_payload()
        ),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNATURE_UNEXPECTED"


def test_invalid_signature_png_has_stable_error(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    payload = certificate_payload(
        template,
        signature=signature_payload(png_data_url="data:image/png;base64,not-png"),
    )

    response = organization_client.post("/certificates", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNATURE_INVALID_PNG"


def test_signature_idempotent_replay_does_not_require_signature(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    headers = {"Idempotency-Key": "signature-replay"}
    first = organization_client.post(
        "/certificates",
        json=certificate_payload(
            template, custom_fields={}, signature=signature_payload()
        ),
        headers=headers,
    )
    second = organization_client.post(
        "/certificates", json=certificate_payload(template), headers=headers
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert db_session.scalar(select(func.count(Certificate.id))) == 1


def test_signature_text_can_extend_beyond_its_block_when_issuing(
    organization_client, db_session, template, certificate_directory
):
    template.positions = {}
    template.layout = signature_layout(height=0.01)
    db_session.flush()
    response = organization_client.post(
        "/certificates",
        json=certificate_payload(
            template,
            custom_fields={},
            signature=signature_payload(
                signer_name="ชื่อยาวมาก " * 15,
                signer_position="ตำแหน่งยาวมาก " * 15,
            ),
        ),
    )

    assert response.status_code == 201, response.text
    assert db_session.scalar(select(func.count(Certificate.id))) == 1
    assert list((p for p in certificate_directory.iterdir() if p.suffix in {".png", ".webp"}))
