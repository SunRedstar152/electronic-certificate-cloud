from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from PIL import Image

from config import settings
from core.errors import register_error_handlers
from database import get_db
from models import (
    Certificate,
    CertificateStatus,
    Organization,
    RecordStatus,
    Template,
    User,
    UserRole,
)
import routers.public as public_module
import services.storage_paths as storage_paths_module
from routers.public import router as public_router


@pytest.fixture(autouse=True)
def certificate_directory(tmp_path, monkeypatch):
    destination = tmp_path / "public-certificates"
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", destination)
    return destination


@pytest.fixture
def organization(db_session):
    value = Organization(name="Public Certificate Organization", code="PUBLIC-ORG")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def issuer(db_session, organization):
    value = User(
        username="public-issuer",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def template(db_session, organization, tmp_path):
    background = tmp_path / "background.png"
    Image.new("RGB", (20, 20), "white").save(background)
    value = Template(
        organization_id=organization.id,
        name="Public Template",
        background_image_path=str(background),
        positions={},
        dimensions={"width": 20, "height": 20},
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def active_certificate(
    db_session, organization, issuer, template, certificate_directory
):
    certificate_directory.mkdir(parents=True)
    image_path = certificate_directory / "public-certificate.png"
    Image.new("RGB", (20, 20), "white").save(image_path)
    value = Certificate(
        organization_id=organization.id,
        template_id=template.id,
        verification_code="PUBLIC-123",
        recipient_reference="INTERNAL-RECIPIENT-REFERENCE",
        recipient_name="Ada Lovelace",
        topic="Public Certificate Testing",
        issue_date=date(2026, 8, 12),
        image_path=str(image_path),
        created_by_id=issuer.id,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def revoked_certificate(active_certificate):
    active_certificate.status = CertificateStatus.revoked
    return active_certificate


@pytest.fixture
def app(db_session):
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(public_router)

    def override_get_db():
        yield db_session

    value.dependency_overrides[get_db] = override_get_db
    return value


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def test_active_certificate_returns_only_public_summary(client, active_certificate):
    response = client.get("/api/public/certificates/public-123")

    assert response.status_code == 200
    assert response.json() == {
        "status": "active",
        "verification_code": "PUBLIC-123",
        "recipient_name": "Ada Lovelace",
        "topic": "Public Certificate Testing",
        "issue_date": "2026-08-12",
        "organization": {"name": "Public Certificate Organization"},
        "recipient_type": "INTERNAL",
    }


def test_revoked_certificate_returns_identifying_summary_without_image_url(
    client, revoked_certificate
):
    response = client.get("/api/public/certificates/PUBLIC-123")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert response.json()["recipient_name"] == "Ada Lovelace"
    assert "image_url" not in response.json()


def test_missing_certificate_has_stable_not_found_error(client):
    response = client.get("/api/public/certificates/unknown-code")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_active_certificate_downloads_stored_file_with_safe_filename(
    client, active_certificate
):
    response = client.get("/api/public/certificates/public-123/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert 'filename="certificate-PUBLIC-123.png"' in response.headers[
        "content-disposition"
    ]


def test_active_certificate_image_is_viewable_inline(client, active_certificate):
    response = client.get("/api/public/certificates/public-123/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "content-disposition" not in response.headers


def test_active_certificate_image_resolves_a_prefixed_relative_storage_path(
    client, active_certificate, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    relative_root = Path("generated/certificates")
    certificate_path = relative_root / "relative-certificate.png"
    certificate_path.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(certificate_path)
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", relative_root)
    active_certificate.image_path = certificate_path.as_posix()

    response = client.get("/api/public/certificates/public-123/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_stored_certificate_path_resolves_a_prefixed_relative_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    relative_root = Path("generated/certificates")
    certificate_path = relative_root / "relative-certificate.png"
    certificate_path.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(certificate_path)
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", relative_root)

    resolved = public_module._stored_certificate_path(certificate_path.as_posix())

    assert resolved == certificate_path.resolve()


def test_stored_certificate_path_allows_an_application_managed_legacy_root(
    client, active_certificate, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    current_root = Path("current/certificates")
    application_root = tmp_path / "application"
    stored_path = Path("static/generated/test/certificates/legacy-certificate.png")
    certificate_path = application_root / stored_path
    certificate_path.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(certificate_path)
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", current_root)
    monkeypatch.setattr(
        storage_paths_module, "APPLICATION_ROOT", application_root
    )
    active_certificate.image_path = stored_path.as_posix()

    resolved = public_module._stored_certificate_path(stored_path.as_posix())

    assert resolved == certificate_path.resolve()

    response = client.get("/api/public/certificates/PUBLIC-123/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_stored_certificate_path_rejects_legacy_relative_traversal_outside_managed_roots(
    tmp_path, monkeypatch
):
    process_directory = tmp_path / "process-directory"
    process_directory.mkdir()
    monkeypatch.chdir(process_directory)
    application_root = tmp_path / "application"
    outside_file = application_root / "static" / "private.png"
    outside_file.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "black").save(outside_file)
    monkeypatch.setattr(
        settings, "CERTIFICATE_DIR", tmp_path / "current/certificates"
    )
    monkeypatch.setattr(storage_paths_module, "APPLICATION_ROOT", application_root)

    resolved = public_module._stored_certificate_path(
        "static/generated/../../private.png"
    )

    assert resolved is None


@pytest.mark.parametrize(
    ("file_format", "content_type", "extension", "signature"),
    [
        ("png", "image/png", "png", b"\x89PNG"),
        ("jpg", "image/jpeg", "jpg", b"\xff\xd8"),
        ("pdf", "application/pdf", "pdf", b"%PDF"),
    ],
)
def test_active_certificate_downloads_in_requested_format(
    client, active_certificate, file_format, content_type, extension, signature
):
    response = client.get(
        f"/api/public/certificates/public-123/download?format={file_format}"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert f'filename="certificate-PUBLIC-123.{extension}"' in response.headers[
        "content-disposition"
    ]
    assert response.content.startswith(signature)


@pytest.mark.parametrize(
    ("file_format", "signature"),
    [("jpg", b"\xff\xd8"), ("pdf", b"%PDF")],
)
def test_certificate_image_conversion_preserves_landscape_output(
    tmp_path, file_format, signature
):
    source_path = tmp_path / "landscape.png"
    Image.new("RGBA", (600, 400), (255, 255, 255, 128)).save(source_path)

    with source_path.open("rb") as source:
        output = public_module._convert_certificate_image(source, file_format)

    assert output.read(4).startswith(signature)


def test_revoked_certificate_image_is_not_viewable(client, revoked_certificate):
    response = client.get("/api/public/certificates/PUBLIC-123/image")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "CERTIFICATE_REVOKED"


def test_revoked_certificate_cannot_be_downloaded(client, revoked_certificate):
    response = client.get("/api/public/certificates/PUBLIC-123/download")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "CERTIFICATE_REVOKED"


def test_download_does_not_serve_a_path_outside_certificate_directory(
    client, active_certificate, tmp_path
):
    outside_file = tmp_path / "private.png"
    Image.new("RGB", (20, 20), "black").save(outside_file)
    active_certificate.image_path = str(outside_file)

    response = client.get("/api/public/certificates/PUBLIC-123/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_download_rejects_a_symlink_inside_certificate_directory(
    client, active_certificate, certificate_directory, tmp_path
):
    outside_file = tmp_path / "private.png"
    Image.new("RGB", (20, 20), "black").save(outside_file)
    image_path = certificate_directory / "linked-certificate.png"
    active_certificate.image_path = str(image_path)
    try:
        image_path.symlink_to(outside_file)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    response = client.get("/api/public/certificates/PUBLIC-123/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_download_keeps_the_validated_file_when_its_path_is_swapped_before_send(
    client, active_certificate, tmp_path, monkeypatch
):
    image_path = Path(active_certificate.image_path)
    expected_content = image_path.read_bytes()
    outside_file = tmp_path / "private.png"
    Image.new("RGB", (20, 20), "black").save(outside_file)
    original_response = FileResponse

    class SwappingFileResponse(original_response):
        def __init__(self, path, *args, **kwargs):
            super().__init__(outside_file, *args, **kwargs)

    monkeypatch.setattr(
        public_module, "FileResponse", SwappingFileResponse, raising=False
    )

    response = client.get("/api/public/certificates/PUBLIC-123/download")

    assert response.status_code == 200
    assert response.content == expected_content
