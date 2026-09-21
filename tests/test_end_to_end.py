from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from auth import get_password_hash
from config import settings
from database import get_db
from main import app
from models import AuditLog, Batch, Certificate, FontAsset, Template, User, UserRole


ADMIN_PASSWORD = "ReleaseAdminPass123!"
ORGANIZATION_PASSWORD = "ReleaseOrganizationPass123!"
POSITIONS = {
    "recipient_name": {"x": 0.5, "y": 0.42},
    "topic": {"x": 0.5, "y": 0.55},
    "issue_date": {"x": 0.25, "y": 0.82},
    "qr_code": {"x": 0.85, "y": 0.8},
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1000, 700), "white").save(output, format="PNG")
    return output.getvalue()


def _xlsx() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["recipient_id", "name", "department"])
    worksheet.append(["001", "Ada Lovelace", "Computing"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _signature_data_url(offset: int) -> str:
    image = Image.new("RGBA", (220, 90), (0, 0, 0, 0))
    try:
        draw = ImageDraw.Draw(image)
        draw.line(
            [(20 + offset, 70), (55 + offset, 20), (90 + offset, 68), (145 + offset, 32), (200, 48)],
            fill=(20, 20, 20, 255),
            width=6,
            joint="curve",
        )
        output = BytesIO()
        image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    finally:
        image.close()


def _signature_layout() -> dict:
    return {
        "version": 2,
        "elements": [
            {
                "id": "signature",
                "source": "signature",
                "kind": "signature",
                "label": "ลายเซ็นผู้ลงนาม",
                "required": True,
                "visible": True,
                "box": {"x": 0.35, "y": 0.66, "width": 0.30, "height": 0.24},
                "style": {
                    "font": {"source": "builtin", "key": "sarabun"},
                    "font_size_px": 18,
                    "color": "#111111",
                    "weight": 400,
                    "align": "center",
                    "line_height": 1.25,
                },
            }
        ],
    }


def _issue_certificate(
    client: TestClient,
    db_session,
    headers: dict[str, str],
    template_id: int,
    recipient_reference: str,
) -> Certificate:
    preview_response = client.post(
        "/batches/preview",
        headers=headers,
        data={
            "template_id": str(template_id),
            "source_type": "xlsx",
            "mapping": json.dumps(
                {
                    "recipient_reference": "recipient_id",
                    "recipient_name": "name",
                    "department": "department",
                }
            ),
        },
        files={
            "file": (
                "release-recipients.xlsx",
                _xlsx(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_rows = preview_response.json()["rows"]
    preview_rows[0]["recipient_reference"] = recipient_reference
    assert preview_rows[0]["errors"] == []

    batch_response = client.post(
        "/batches",
        headers=headers,
        json={
            "template_id": template_id,
            "topic": "Release Validation",
            "issue_date": "2026-08-13",
            "source_type": "xlsx",
            "event_metadata": {"purpose": "release-gate"},
            "rows": preview_rows,
        },
    )
    assert batch_response.status_code == 201, batch_response.text
    batch_id = batch_response.json()["id"]

    process_response = client.post(
        f"/batches/{batch_id}/process", headers=headers
    )
    assert process_response.status_code == 200, process_response.text
    processed = process_response.json()
    assert processed["status"] == "completed"
    assert processed["success_count"] == 1
    assert processed["failure_count"] == 0
    certificate = db_session.get(Certificate, processed["rows"][0]["certificate_id"])
    assert certificate is not None
    return certificate


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMPLATE_DIR", tmp_path / "templates")
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", tmp_path / "certificates")
    monkeypatch.setattr(settings, "ENABLE_SCHEDULER", False)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_complete_certificate_lifecycle(client, db_session):
    suffix = uuid4().hex[:10]
    admin = User(
        username=f"release-admin-{suffix}",
        password_hash=get_password_hash(ADMIN_PASSWORD),
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()

    admin_token = _login(client, admin.username, ADMIN_PASSWORD)
    organization_username = f"release-org-{suffix}"
    create_response = client.post(
        "/admin/organizations",
        headers=_bearer(admin_token),
        json={
            "name": "Release Validation Organization",
            "username": organization_username,
            "password": ORGANIZATION_PASSWORD,
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    organization_id = created["organization"]["id"]
    predecessor_id = created["account"]["id"]
    assert "code" not in created["organization"]

    predecessor_token = _login(
        client, organization_username, ORGANIZATION_PASSWORD
    )
    predecessor_headers = _bearer(predecessor_token)
    template_response = client.post(
        "/templates",
        headers=predecessor_headers,
        data={
            "name": "Release Certificate Template",
            "positions": json.dumps(POSITIONS),
        },
        files={"file": ("release-template.png", _png(), "image/png")},
    )
    assert template_response.status_code == 201, template_response.text
    template_id = template_response.json()["id"]

    resource_counts_before_successor = {
        "templates": db_session.scalar(
            select(func.count(Template.id)).where(
                Template.organization_id == organization_id
            )
        ),
        "fonts": db_session.scalar(
            select(func.count(FontAsset.id)).where(
                FontAsset.organization_id == organization_id
            )
        ),
        "batches": db_session.scalar(
            select(func.count(Batch.id)).where(Batch.organization_id == organization_id)
        ),
        "certificates": db_session.scalar(
            select(func.count(Certificate.id)).where(
                Certificate.organization_id == organization_id
            )
        ),
    }
    successor_username = f"release-successor-{suffix}"
    successor_response = client.post(
        f"/admin/organizations/{organization_id}/users",
        headers=_bearer(admin_token),
        json={"username": successor_username, "password": ORGANIZATION_PASSWORD},
    )
    assert successor_response.status_code == 201, successor_response.text
    successor_id = successor_response.json()["account"]["id"]
    assert successor_response.json()["account"]["organization_id"] == organization_id
    assert {
        "templates": db_session.scalar(
            select(func.count(Template.id)).where(
                Template.organization_id == organization_id
            )
        ),
        "fonts": db_session.scalar(
            select(func.count(FontAsset.id)).where(
                FontAsset.organization_id == organization_id
            )
        ),
        "batches": db_session.scalar(
            select(func.count(Batch.id)).where(Batch.organization_id == organization_id)
        ),
        "certificates": db_session.scalar(
            select(func.count(Certificate.id)).where(
                Certificate.organization_id == organization_id
            )
        ),
    } == resource_counts_before_successor

    successor_headers = _bearer(
        _login(client, successor_username, ORGANIZATION_PASSWORD)
    )
    assert client.get(
        f"/templates/{template_id}", headers=predecessor_headers
    ).status_code == 200
    assert client.get(
        f"/templates/{template_id}", headers=successor_headers
    ).status_code == 200

    predecessor_certificate = _issue_certificate(
        client, db_session, predecessor_headers, template_id, "001"
    )
    successor_certificate = _issue_certificate(
        client, db_session, successor_headers, template_id, "002"
    )

    deactivate_response = client.patch(
        f"/admin/organizations/{organization_id}/users/{predecessor_id}",
        headers=_bearer(admin_token),
        json={"is_active": False},
    )
    assert deactivate_response.status_code == 200, deactivate_response.text
    assert client.get("/templates", headers=predecessor_headers).status_code == 401
    assert client.get("/templates", headers=successor_headers).status_code == 200

    continued_certificate = _issue_certificate(
        client, db_session, successor_headers, template_id, "003"
    )
    certificates = [
        predecessor_certificate,
        successor_certificate,
        continued_certificate,
    ]
    predecessor = db_session.get(User, predecessor_id)
    successor = db_session.get(User, successor_id)
    assert predecessor is not None
    assert successor is not None
    assert {
        predecessor.organization_id,
        successor.organization_id,
        *(certificate.organization_id for certificate in certificates),
    } == {organization_id}
    assert db_session.scalar(
        select(func.count(Template.id)).where(Template.organization_id == organization_id)
    ) == resource_counts_before_successor["templates"]
    assert list(
        db_session.scalars(
            select(Template.id).where(Template.organization_id == organization_id, Template.builtin_key.is_(None))
        )
    ) == [template_id]
    assert db_session.scalar(
        select(func.count(Batch.id)).where(Batch.organization_id == organization_id)
    ) == resource_counts_before_successor["batches"] + 3
    assert db_session.scalar(
        select(func.count(Certificate.id)).where(
            Certificate.organization_id == organization_id
        )
    ) == resource_counts_before_successor["certificates"] + 3

    for certificate in certificates:
        code = certificate.verification_code
        assert re.fullmatch(r"[A-Z0-9]{16}", code)
        verify_response = client.get(f"/api/public/certificates/{code}")
        assert verify_response.status_code == 200, verify_response.text
        assert verify_response.json()["status"] == "active"
        assert "recipient_reference" not in verify_response.json()
        download_response = client.get(f"/api/public/certificates/{code}/download")
        assert download_response.status_code == 200
        assert download_response.headers["content-type"] == "image/png"

    certificate = predecessor_certificate
    code = certificate.verification_code

    revoke_response = client.post(
        f"/certificates/{code}/revoke",
        headers=successor_headers,
        json={"reason": "Issued to the wrong recipient."},
    )
    assert revoke_response.status_code == 200, revoke_response.text
    assert revoke_response.json()["status"] == "revoked"

    revoked_verify = client.get(f"/api/public/certificates/{code}")
    assert revoked_verify.status_code == 200
    assert revoked_verify.json()["status"] == "revoked"
    assert "image_url" not in revoked_verify.json()
    revoked_download = client.get(
        f"/api/public/certificates/{code}/download"
    )
    assert revoked_download.status_code == 410
    assert revoked_download.json()["error"]["code"] == "CERTIFICATE_REVOKED"

    revoke_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "certificate.revoke",
                AuditLog.target_id == str(certificate.id),
            )
        )
    )
    assert len(revoke_audits) == 1
    assert revoke_audits[0].organization_id == organization_id


def test_transient_signatures_render_for_single_and_bulk_without_persistence(
    client, db_session, caplog, tmp_path
):
    suffix = uuid4().hex[:10]
    admin = User(
        username=f"signature-admin-{suffix}",
        password_hash=get_password_hash(ADMIN_PASSWORD),
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    admin_token = _login(client, admin.username, ADMIN_PASSWORD)
    organization_username = f"signature-org-{suffix}"
    organization_response = client.post(
        "/admin/organizations",
        headers=_bearer(admin_token),
        json={
            "name": f"Signature Organization {suffix}",
            "username": organization_username,
            "password": ORGANIZATION_PASSWORD,
        },
    )
    assert organization_response.status_code == 201, organization_response.text
    headers = _bearer(_login(client, organization_username, ORGANIZATION_PASSWORD))

    template_response = client.post(
        "/templates",
        headers=headers,
        data={
            "name": "Transient Signature Template",
            "positions": "{}",
            "layout": json.dumps(_signature_layout()),
        },
        files={"file": ("signature-template.png", _png(), "image/png")},
    )
    assert template_response.status_code == 201, template_response.text
    template_id = template_response.json()["id"]
    filesystem_before = {
        path.resolve()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    single_signature_data_url = _signature_data_url(0)
    batch_signature_data_url = _signature_data_url(18)

    single_response = client.post(
        "/certificates",
        headers=headers,
        json={
            "template_id": template_id,
            "recipient_reference": "SINGLE-001",
            "recipient_name": "ผู้รับเดี่ยว",
            "topic": "กิจกรรมเดี่ยว",
            "issue_date": "2026-09-04",
            "signature": {
                "png_data_url": single_signature_data_url,
                "signer_name": "ผู้ลงนาม",
                "signer_position": "คณบดี",
            },
        },
    )
    assert single_response.status_code == 201, single_response.text

    batch_response = client.post(
        "/batches",
        headers=headers,
        json={
            "template_id": template_id,
            "topic": "กิจกรรมกลุ่ม",
            "issue_date": "2026-09-04",
            "source_type": "xlsx",
            "event_metadata": {},
            "rows": [
                {"row_number": 1, "recipient_reference": "BATCH-001", "recipient_name": "ผู้รับหนึ่ง", "custom_fields": {}, "errors": []},
                {"row_number": 2, "recipient_reference": "BATCH-002", "recipient_name": "ผู้รับสอง", "custom_fields": {}, "errors": []},
            ],
        },
    )
    assert batch_response.status_code == 201, batch_response.text
    process_response = client.post(
        f"/batches/{batch_response.json()['id']}/process",
        headers=headers,
        json={
            "signature": {
                "png_data_url": batch_signature_data_url,
                "signer_name": "ผู้ลงนาม",
                "signer_position": "คณบดี",
            }
        },
    )
    assert process_response.status_code == 200, process_response.text
    assert process_response.json()["success_count"] == 2
    assert db_session.scalar(select(func.count(Certificate.id))) == 3
    assert not any("signature" in column.name for column in Certificate.__table__.columns)
    assert not any("signature" in column.name for column in Batch.__table__.columns)

    stored_certificates = list(db_session.scalars(select(Certificate).order_by(Certificate.id)))
    assert sorted((p for p in settings.CERTIFICATE_DIR.iterdir() if p.suffix in {".png", ".webp"})) == sorted(
        (Path(item.image_path) for item in stored_certificates), key=str
    )
    filesystem_after = {
        path.resolve()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert filesystem_after - filesystem_before == {
        Path(item.image_path).resolve() for item in stored_certificates
    }
    assert single_signature_data_url not in caplog.text
    assert batch_signature_data_url not in caplog.text

    def signature_region(path: str) -> bytes:
        with Image.open(path) as image:
            left = round(0.35 * image.width)
            top = round(0.66 * image.height)
            right = round(0.65 * image.width)
            bottom = round(0.90 * image.height)
            return image.convert("RGB").crop((left, top, right, bottom)).tobytes()

    single_region = signature_region(stored_certificates[0].image_path)
    batch_regions = [signature_region(item.image_path) for item in stored_certificates[1:]]
    assert batch_regions[0] == batch_regions[1]
    assert single_region != batch_regions[0]
