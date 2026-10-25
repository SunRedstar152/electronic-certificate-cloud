from __future__ import annotations

import base64
import csv
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
from pathlib import Path
import threading
from unittest.mock import Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import sessionmaker

from auth import create_access_token
from config import settings
from core.errors import register_error_handlers
from database import get_db
from models import (
    AuditLog,
    Batch,
    BatchRow,
    BatchStatus,
    Certificate,
    CertificateStatus,
    Organization,
    RecordStatus,
    Template,
    User,
    UserRole,
)
from routers.batches import router as batches_router
from schemas import SignatureSubmission
import services.batch_service as batch_service_module
import services.storage_paths as storage_paths_module
from services.batch_service import confirm_batch, process_batch, retry_failed_rows


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
    value = Organization(name="Batch Organization", code="BATCH-ORG")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization(db_session):
    value = Organization(name="Other Batch Organization", code="BATCH-OTHER")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def organization_user(db_session, organization):
    value = User(
        username="batch-user",
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
        username="other-batch-user",
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
        username="batch-admin",
        password_hash="hash",
        role=UserRole.admin,
        organization_id=None,
        is_active=True,
    )
    db_session.add(value)
    db_session.flush()
    return value


def _template(db_session, organization, tmp_path, name):
    background = tmp_path / f"{name}.png"
    Image.new("RGB", (600, 400), "white").save(background)
    value = Template(
        organization_id=organization.id,
        name=name,
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 600, "height": 400},
        status=RecordStatus.active,
    )
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def template(db_session, organization, tmp_path):
    return _template(db_session, organization, tmp_path, "Batch Template")


@pytest.fixture
def other_template(db_session, other_organization, tmp_path):
    return _template(db_session, other_organization, tmp_path, "Other Batch Template")


@pytest.fixture(autouse=True)
def certificate_directory(tmp_path, monkeypatch):
    destination = tmp_path / "batch-certificates"
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", destination)
    return destination


@pytest.fixture
def app(db_session):
    value = FastAPI()
    register_error_handlers(value)
    value.include_router(batches_router)

    def override_get_db():
        yield db_session

    value.dependency_overrides[get_db] = override_get_db
    return value


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def organization_client(client, organization_user):
    client.headers.update(authorization(organization_user))
    return client


@pytest.fixture
def other_client(client, other_user):
    client.headers.update(authorization(other_user))
    return client


@pytest.fixture
def admin_client(client, admin_user):
    client.headers.update(authorization(admin_user))
    return client


def valid_row(reference="RECIPIENT-001", name="Ada Lovelace", row_number=2):
    return {
        "row_number": row_number,
        "recipient_reference": reference,
        "recipient_name": name,
        "custom_fields": {"department": "Computing"},
        "errors": [],
    }


def signature_valid_row(reference="RECIPIENT-001", name="Ada Lovelace", row_number=2):
    row = valid_row(reference, name, row_number)
    row["custom_fields"] = {}
    return row


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
                    "font_size_px": 18,
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


def signature_submission():
    return SignatureSubmission(**signature_payload())


def invalid_row(row_number=3):
    return {
        "row_number": row_number,
        "recipient_reference": "RECIPIENT-INVALID",
        "recipient_name": None,
        "custom_fields": {},
        "errors": [],
    }


def batch_payload(template, rows, **overrides):
    payload = {
        "template_id": template.id,
        "topic": "Bulk Certificate Reliability",
        "issue_date": "2026-08-12",
        "source_type": "xlsx",
        "event_metadata": {"event": "PKRU Engineering Day", "year": 2026},
        "rows": rows,
    }
    payload.update(overrides)
    return payload


def create_batch(client, template, rows, **overrides):
    response = client.post("/batches", json=batch_payload(template, rows, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_recipient_history_pagination_search_and_scope(organization_client, template, other_user, admin_user):
    batch = create_batch(organization_client, template, [
        valid_row(f"REF-{i}", f"Recipient {i}", i + 1) for i in range(12)
    ])
    url = f"/batches/{batch['id']}/recipients"
    page = organization_client.get(url, params={"offset": 10, "limit": 10})
    assert page.status_code == 200
    assert page.json()["total"] == 12
    assert [r["row_number"] for r in page.json()["items"]] == [11, 12]
    found = organization_client.get(url, params={"search": "REF-11"}).json()
    assert found["total"] == 1
    assert found["items"][0]["recipient_name"] == "Recipient 11"
    assert organization_client.get(url, params={"search": "%"}).json()["total"] == 0
    assert organization_client.get(url, params={"limit": 101}).status_code == 422
    assert organization_client.get(url, headers=authorization(other_user)).status_code == 404
    assert organization_client.get(url, headers=authorization(admin_user)).status_code == 403
    organization_client.headers.clear()
    assert organization_client.get(url).status_code == 401


def test_recipient_history_shows_current_revocation_and_deleted_certificate(organization_client, organization_user, template, db_session):
    from services.certificates import revoke_certificate, delete_certificate
    batch = create_batch(organization_client, template, [valid_row()])
    assert organization_client.post(f"/batches/{batch['id']}/process").status_code == 200
    url = f"/batches/{batch['id']}/recipients"
    item = organization_client.get(url).json()["items"][0]
    assert item["status"] == "success"
    assert item["certificate_status"] == "active"
    code = item["verification_code"]
    revoke_certificate(db_session, organization_user, code, "Incorrect recipient")
    assert organization_client.get(url).json()["items"][0]["certificate_status"] == "revoked"
    delete_certificate(db_session, organization_user, code)
    item = organization_client.get(url).json()["items"][0]
    assert item["recipient_name"] == "Ada Lovelace"
    assert item["status"] == "success"
    assert item["verification_code"] is None


def xlsx_file(rows):
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return ("recipients.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_preview_parses_and_maps_without_persisting_or_issuing(
    organization_client, db_session, template
):
    response = organization_client.post(
        "/batches/preview",
        data={
            "template_id": str(template.id),
            "source_type": "xlsx",
            "mapping": json.dumps(
                {
                    "recipient_reference": "recipient_id",
                    "recipient_name": "name",
                    "department": "department",
                }
            ),
        },
        files={"file": xlsx_file([
            ["recipient_id", "name", "department"],
            ["001", "Ada Lovelace", "Computing"],
            ["002", "", "Mathematics"],
        ])},
    )

    assert response.status_code == 200, response.text
    assert response.json()["headers"] == ["recipient_id", "name", "department"]
    assert response.json()["rows"][0]["recipient_reference"] == "001"
    assert response.json()["rows"][1]["errors"][0]["code"] == "IMPORT_REQUIRED_VALUE"
    assert db_session.scalar(select(func.count(Batch.id))) == 0
    assert db_session.scalar(select(func.count(Certificate.id))) == 0


def _dynamic_batch_layout():
    return {
        "version": 2,
        "elements": [
            {
                "id": "training_period",
                "source": "custom.training_period",
                "kind": "date_range",
                "label": "ระยะเวลาอบรม",
                "required": True,
                "visible": True,
                "box": {"x": 0.1, "y": 0.4, "width": 0.8, "height": 0.18},
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
            }
        ],
    }


def test_template_range_field_preview_confirm_and_process_use_same_shape(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = _dynamic_batch_layout()
    db_session.flush()
    preview = organization_client.post(
        "/batches/preview",
        data={
            "template_id": str(template.id),
            "source_type": "xlsx",
            "mapping": json.dumps(
                {
                    "recipient_reference": "recipient_id",
                    "recipient_name": "name",
                    "custom.training_period.start": "start_date",
                    "custom.training_period.end": "end_date",
                }
            ),
        },
        files={"file": xlsx_file([
            ["recipient_id", "name", "start_date", "end_date"],
            ["001", "ผู้เข้าอบรม", "2026-08-13", "2026-08-15"],
        ])},
    )

    assert preview.status_code == 200, preview.text
    row = preview.json()["rows"][0]
    assert row["custom_fields"]["training_period"] == {
        "start": "2026-08-13",
        "end": "2026-08-15",
    }
    batch = create_batch(organization_client, template, [row])
    processed = organization_client.post(f"/batches/{batch['id']}/process")
    assert processed.status_code == 200, processed.text
    certificate = db_session.get(
        Certificate, processed.json()["rows"][0]["certificate_id"]
    )
    assert certificate.custom_fields["training_period"] == {
        "start": "2026-08-13",
        "end": "2026-08-15",
    }


def test_main_mounts_batch_routes_and_registers_safe_error_handlers():
    from main import app as main_app

    route_paths = set(main_app.openapi()["paths"])
    assert "/batches/preview" in route_paths
    assert "/batches/{batch_id}/process" in route_paths
    assert "/verify/{code}" not in route_paths
    assert RequestValidationError in main_app.exception_handlers


def test_confirmation_revalidates_rows_and_freezes_shared_metadata(
    organization_client, db_session, organization_user, template
):
    payload = batch_payload(template, [valid_row(), invalid_row()])
    response = organization_client.post("/batches", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_id"] == organization_user.organization_id
    assert body["status"] == BatchStatus.validated.value
    assert body["event_metadata"] == payload["event_metadata"]
    assert body["rows"][1]["status"] == "failed"
    assert body["rows"][1]["error_code"] == "IMPORT_REQUIRED_VALUE"

    payload["event_metadata"]["event"] = "Changed after confirmation"
    payload["rows"][0]["recipient_name"] = "Changed after confirmation"
    db_session.expire_all()
    stored = db_session.get(Batch, body["id"])
    assert stored.event_metadata["event"] == "PKRU Engineering Day"
    assert stored.rows[0].recipient_name == "Ada Lovelace"


def test_batch_keeps_row_level_partial_success_and_is_idempotent(
    organization_client, db_session, template
):
    batch = create_batch(organization_client, template, [valid_row(), invalid_row()])

    first = organization_client.post(f"/batches/{batch['id']}/process")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == BatchStatus.partial.value
    assert body["success_count"] == 1
    assert body["failure_count"] == 1
    assert body["rows"][1]["error_code"] == "IMPORT_REQUIRED_VALUE"
    successful_id = body["rows"][0]["certificate_id"]
    successful_code = db_session.get(Certificate, successful_id).verification_code

    second = organization_client.post(f"/batches/{batch['id']}/process")

    assert second.status_code == 200
    assert second.json()["rows"][0]["certificate_id"] == successful_id
    assert db_session.get(Certificate, successful_id).verification_code == successful_code
    assert db_session.scalar(select(func.count(Certificate.id))) == 1


def test_retry_claims_failed_rows_only(
    organization_client, db_session, template, monkeypatch
):
    batch = create_batch(
        organization_client,
        template,
        [
            valid_row("RETRY-GOOD", "First Recipient", 2),
            invalid_row(3) | {"recipient_reference": "RETRY-BAD"},
        ],
    )
    processed = organization_client.post(f"/batches/{batch['id']}/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == BatchStatus.partial.value
    successful_row = next(row for row in processed.json()["rows"] if row["status"] == "success")
    successful_certificate = db_session.get(Certificate, successful_row["certificate_id"])
    original_code = successful_certificate.verification_code
    failed_row = db_session.scalar(
        select(BatchRow).where(
            BatchRow.batch_id == batch["id"],
            BatchRow.status == "failed",
        )
    )
    failed_row.recipient_name = "Second Recipient"
    db_session.commit()

    tracked_create = Mock(wraps=batch_service_module.create_certificate)
    monkeypatch.setattr(batch_service_module, "create_certificate", tracked_create)
    retried = organization_client.post(f"/batches/{batch['id']}/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == BatchStatus.completed.value
    assert tracked_create.call_count == 1
    assert tracked_create.call_args.args[2].recipient_reference == "RETRY-BAD"
    assert db_session.get(Certificate, successful_row["certificate_id"]).verification_code == original_code
    assert db_session.scalar(select(func.count(Certificate.id))) == 2


def test_batch_routes_are_organization_scoped_and_reject_admins(
    client, organization_user, other_user, admin_user, template
):
    client.headers.update(authorization(organization_user))
    batch = create_batch(client, template, [valid_row()])

    other_get = client.get(
        f"/batches/{batch['id']}", headers=authorization(other_user)
    )
    other_process = client.post(
        f"/batches/{batch['id']}/process", headers=authorization(other_user)
    )
    admin_get = client.get(
        f"/batches/{batch['id']}", headers=authorization(admin_user)
    )

    assert other_get.status_code == 404
    assert other_get.json()["error"]["code"] == "BATCH_NOT_FOUND"
    assert other_process.status_code == 404
    assert admin_get.status_code == 403
    assert admin_get.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_processing_claims_batch_rows_with_skip_locked(
    organization_client, db_session, template
):
    batch = create_batch(organization_client, template, [valid_row()])
    statements = []
    engine = db_session.get_bind().engine

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = organization_client.post(f"/batches/{batch['id']}/process")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200
    assert any(
        "FROM BATCH_ROWS" in statement
        and "FOR UPDATE" in statement
        and "SKIP LOCKED" in statement
        for statement in statements
    )


def _committed_batch_context(tmp_path, db_session, row_count=4):
    # Reuse the fixture engine; importing conftest again creates an unmanaged pool.
    session_factory = sessionmaker(bind=db_session.get_bind().engine, expire_on_commit=False)
    setup = session_factory()
    suffix = uuid4().hex
    background = tmp_path / f"concurrent-{suffix}.png"
    Image.new("RGB", (600, 400), "white").save(background)
    organization = Organization(
        name=f"Concurrent Batch {suffix}", code=f"CON-{suffix[:20]}"
    )
    setup.add(organization)
    setup.flush()
    actor = User(
        username=f"concurrent-batch-{suffix}",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    template = Template(
        organization_id=organization.id,
        name="Concurrent Template",
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 600, "height": 400},
        status=RecordStatus.active,
    )
    setup.add_all([actor, template])
    setup.flush()
    batch = confirm_batch(
        setup,
        actor,
        {
            "template_id": template.id,
            "topic": "Concurrent batch processing",
            "issue_date": date(2026, 8, 12),
            "source_type": "xlsx",
            "event_metadata": {},
        },
        [
            valid_row(f"CONCURRENT-{number}", f"Recipient {number}", number + 1)
            for number in range(1, row_count + 1)
        ],
    )
    result = session_factory, organization.id, actor.id, batch.id
    setup.close()
    return result


def _cleanup_committed_batch(session_factory, organization_id):
    cleanup = session_factory()
    try:
        paths = list(
            cleanup.scalars(
                select(Certificate.image_path).where(
                    Certificate.organization_id == organization_id
                )
            )
        )
        cleanup.execute(delete(AuditLog).where(AuditLog.organization_id == organization_id))
        cleanup.execute(
            delete(BatchRow).where(
                BatchRow.batch_id.in_(
                    select(Batch.id).where(Batch.organization_id == organization_id)
                )
            )
        )
        cleanup.execute(delete(Certificate).where(Certificate.organization_id == organization_id))
        cleanup.execute(delete(Batch).where(Batch.organization_id == organization_id))
        cleanup.execute(delete(Template).where(Template.organization_id == organization_id))
        cleanup.execute(delete(User).where(User.organization_id == organization_id))
        cleanup.execute(delete(Organization).where(Organization.id == organization_id))
        cleanup.commit()
    finally:
        cleanup.close()
    for value in paths:
        if value:
            Path(value).unlink(missing_ok=True)


def test_concurrent_workers_keep_counters_and_certificates_exactly_once(
    db_session, tmp_path
):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(tmp_path, db_session)
    barrier = threading.Barrier(2)
    errors = []

    def run_worker():
        session = session_factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait(timeout=5)
            process_batch(session, actor, batch_id)
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    workers = [threading.Thread(target=run_worker) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)

        assert all(not worker.is_alive() for worker in workers)
        assert errors == []
        verification = session_factory()
        try:
            batch = verification.get(Batch, batch_id)
            assert batch.status == BatchStatus.completed
            assert (batch.total_count, batch.success_count, batch.failure_count) == (4, 4, 0)
            assert verification.scalar(
                select(func.count(Certificate.id)).where(
                    Certificate.organization_id == organization_id,
                    Certificate.batch_id == batch_id,
                )
            ) == 4
            assert len(
                set(
                    verification.scalars(
                        select(Certificate.idempotency_key).where(
                            Certificate.batch_id == batch_id
                        )
                    )
                )
            ) == 4
        finally:
            verification.close()
    finally:
        for worker in workers:
            worker.join(timeout=1)
        _cleanup_committed_batch(session_factory, organization_id)


def test_stale_processing_row_is_reclaimed_after_worker_crash(db_session, tmp_path):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(
        tmp_path, db_session, row_count=1
    )
    setup = session_factory()
    try:
        row = setup.scalar(select(BatchRow).where(BatchRow.batch_id == batch_id))
        row.status = "processing"
        row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        batch = setup.get(Batch, batch_id)
        batch.status = BatchStatus.processing
        setup.commit()
    finally:
        setup.close()

    worker = session_factory()
    try:
        actor = worker.get(User, actor_id)
        result = process_batch(worker, actor, batch_id)
        assert result.status == BatchStatus.completed
        assert result.success_count == 1
    finally:
        worker.close()
        _cleanup_committed_batch(session_factory, organization_id)


def test_retry_claims_failed_rows_without_recovering_stale_processing(
    db_session, tmp_path
):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(
        tmp_path, db_session, row_count=2
    )
    setup = session_factory()
    try:
        rows = list(
            setup.scalars(
                select(BatchRow)
                .where(BatchRow.batch_id == batch_id)
                .order_by(BatchRow.id)
            )
        )
        stale_row, failed_row = rows
        stale_row.status = "processing"
        stale_row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        failed_row.status = "failed"
        failed_row.error_code = "CERTIFICATE_ISSUANCE_FAILED"
        failed_row.error_message = "The certificate could not be issued."
        batch = setup.get(Batch, batch_id)
        batch.status = BatchStatus.partial
        batch.failure_count = 1
        setup.commit()
        stale_row_id = stale_row.id
        failed_row_id = failed_row.id
    finally:
        setup.close()

    worker = session_factory()
    try:
        actor = worker.get(User, actor_id)
        retried = retry_failed_rows(worker, actor, batch_id)
        worker.expire_all()
        stale_row = worker.get(BatchRow, stale_row_id)
        failed_row = worker.get(BatchRow, failed_row_id)

        assert retried.status == BatchStatus.processing
        assert (retried.success_count, retried.failure_count) == (1, 0)
        assert stale_row.status == "processing"
        assert stale_row.certificate_id is None
        assert failed_row.status == "success"
        assert failed_row.certificate_id is not None
        assert worker.scalar(
            select(func.count(Certificate.id)).where(Certificate.batch_id == batch_id)
        ) == 1

        processed = process_batch(worker, actor, batch_id)
        assert processed.status == BatchStatus.completed
        assert processed.success_count == 2
    finally:
        worker.close()
        _cleanup_committed_batch(session_factory, organization_id)


def test_fresh_processing_row_is_not_stolen(db_session, tmp_path):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(
        tmp_path, db_session, row_count=1
    )
    setup = session_factory()
    try:
        row = setup.scalar(select(BatchRow).where(BatchRow.batch_id == batch_id))
        row.status = "processing"
        row.updated_at = datetime.now(timezone.utc)
        batch = setup.get(Batch, batch_id)
        batch.status = BatchStatus.processing
        setup.commit()
    finally:
        setup.close()

    worker = session_factory()
    try:
        actor = worker.get(User, actor_id)
        result = process_batch(worker, actor, batch_id)
        assert result.status == BatchStatus.processing
        assert result.success_count == 0
        assert worker.scalar(
            select(func.count(Certificate.id)).where(Certificate.batch_id == batch_id)
        ) == 0
    finally:
        worker.close()
        _cleanup_committed_batch(session_factory, organization_id)


def test_aborted_issuance_transaction_is_rolled_back_before_failure_persistence(
    db_session, tmp_path, monkeypatch
):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(
        tmp_path, db_session, row_count=1
    )

    def abort_transaction(db, _actor, _payload, _idempotency_key):
        db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(batch_service_module, "create_certificate", abort_transaction)
    worker = session_factory()
    try:
        actor = worker.get(User, actor_id)
        result = process_batch(worker, actor, batch_id)
        assert result.status == BatchStatus.failed
        row = worker.scalar(select(BatchRow).where(BatchRow.batch_id == batch_id))
        assert row.status == "failed"
        assert row.error_code == "CERTIFICATE_ISSUANCE_FAILED"
        assert row.error_message == "The certificate could not be issued."
    finally:
        worker.close()
        _cleanup_committed_batch(session_factory, organization_id)


@pytest.mark.parametrize("failure_kind", ["flush", "post_mutation"])
def test_failed_issuance_discards_pending_certificate_and_audit_mutations(
    db_session, tmp_path, monkeypatch, failure_kind
):
    session_factory, organization_id, actor_id, batch_id = _committed_batch_context(
        tmp_path, db_session, row_count=1
    )

    def fail_after_mutation(db, actor, payload, idempotency_key):
        certificate = Certificate(
            organization_id=actor.organization_id,
            batch_id=payload.batch_id,
            template_id=payload.template_id,
            verification_code=f"FAILED-{failure_kind}",
            recipient_reference=payload.recipient_reference,
            recipient_name=payload.recipient_name,
            topic=payload.topic,
            issue_date=payload.issue_date,
            idempotency_key=idempotency_key,
            created_by_id=actor.id,
        )
        db.add(certificate)
        db.add(
            AuditLog(
                actor_id=actor.id,
                organization_id=actor.organization_id,
                action="certificate.create",
                target_type="certificate",
                details={"must": "rollback"},
            )
        )
        if failure_kind == "flush":
            db.execute(text("SELECT 1 / 0"))
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(batch_service_module, "create_certificate", fail_after_mutation)
    worker = session_factory()
    try:
        actor = worker.get(User, actor_id)
        result = process_batch(worker, actor, batch_id)
        assert result.status == BatchStatus.failed
        row = worker.scalar(select(BatchRow).where(BatchRow.batch_id == batch_id))
        assert row.error_code == "CERTIFICATE_ISSUANCE_FAILED"
        assert row.error_message == "The certificate could not be issued."
        assert worker.scalar(
            select(func.count(Certificate.id)).where(Certificate.batch_id == batch_id)
        ) == 0
        assert worker.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.organization_id == organization_id,
                AuditLog.action == "certificate.create",
            )
        ) == 0
        assert worker.scalar(select(func.count(BatchRow.id))) >= 1
    finally:
        worker.close()
        _cleanup_committed_batch(session_factory, organization_id)


def _manifest_rows(data):
    return list(csv.DictReader(StringIO(data.decode("utf-8-sig"))))


def test_zip_streams_safe_active_images_and_manifests_omissions(
    organization_client, db_session, template, certificate_directory, tmp_path
):
    rows = [
        valid_row("../../Ada", "Ada", 2),
        valid_row("REVOKED", "Revoked", 3),
        valid_row("MISSING", "Missing", 4),
        valid_row("UNSAFE", "Unsafe", 5),
    ]
    batch = create_batch(organization_client, template, rows)
    processed = organization_client.post(f"/batches/{batch['id']}/process")
    assert processed.status_code == 200, processed.text
    certificates = {
        certificate.recipient_reference: certificate
        for certificate in db_session.scalars(
            select(Certificate).where(Certificate.batch_id == batch["id"])
        )
    }
    certificates["REVOKED"].status = CertificateStatus.revoked
    Path(certificates["MISSING"].image_path).unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not an owned certificate image")
    certificates["UNSAFE"].image_path = str(outside)
    db_session.commit()

    response = organization_client.get(f"/batches/{batch['id']}/download")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names
        image_names = [name for name in names if name != "manifest.csv"]
        assert len(image_names) == 1
        with Image.open(BytesIO(archive.read(image_names[0]))) as exported:
            assert exported.format == "PNG"
        assert "/" not in image_names[0]
        assert "\\" not in image_names[0]
        assert ".." not in image_names[0]
        assert image_names[0].endswith(
            f"-{certificates['../../Ada'].verification_code}.png"
        )
        manifest = _manifest_rows(archive.read("manifest.csv"))

    assert {row["recipient_reference"] for row in manifest} == {
        "REVOKED",
        "MISSING",
        "UNSAFE",
    }
    reasons = {row["recipient_reference"]: row["reason"] for row in manifest}
    assert reasons == {
        "REVOKED": "CERTIFICATE_REVOKED",
        "MISSING": "CERTIFICATE_IMAGE_MISSING",
        "UNSAFE": "CERTIFICATE_IMAGE_PATH_UNSAFE",
    }


def test_zip_includes_certificate_from_legacy_storage(
    organization_client, db_session, template, tmp_path, monkeypatch
):
    process_directory = tmp_path / "process-directory"
    process_directory.mkdir()
    monkeypatch.chdir(process_directory)
    batch = create_batch(
        organization_client,
        template,
        [valid_row("LEGACY", "Legacy Recipient", 2)],
    )
    processed = organization_client.post(f"/batches/{batch['id']}/process")
    assert processed.status_code == 200, processed.text
    certificate = db_session.scalar(
        select(Certificate).where(Certificate.batch_id == batch["id"])
    )
    application_root = tmp_path / "application"
    stored_path = Path("static/generated/test/certificates/legacy.png")
    legacy_image = application_root / stored_path
    legacy_image.parent.mkdir(parents=True)
    Path(certificate.image_path).replace(legacy_image)
    certificate.image_path = stored_path.as_posix()
    db_session.commit()
    monkeypatch.setattr(
        storage_paths_module,
        "APPLICATION_ROOT",
        application_root,
    )

    response = organization_client.get(f"/batches/{batch['id']}/download")

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        image_names = [name for name in archive.namelist() if name != "manifest.csv"]
        assert len(image_names) == 1
        with Image.open(BytesIO(archive.read(image_names[0]))) as exported:
            assert exported.format == "PNG"


def test_bulk_signature_is_validated_before_batch_state_changes(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    batch = create_batch(
        organization_client, template, [signature_valid_row()]
    )

    response = organization_client.post(f"/batches/{batch['id']}/process")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNATURE_REQUIRED"
    db_session.expire_all()
    stored = db_session.get(Batch, batch["id"])
    assert stored.status == BatchStatus.validated
    assert db_session.scalar(select(func.count(Certificate.id))) == 0


def test_plain_batch_rejects_an_unexpected_signature_without_state_change(
    organization_client, db_session, template
):
    batch = create_batch(
        organization_client, template, [valid_row()]
    )

    response = organization_client.post(
        f"/batches/{batch['id']}/process",
        json={"signature": signature_payload()},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNATURE_UNEXPECTED"
    db_session.expire_all()
    assert db_session.get(Batch, batch["id"]).status == BatchStatus.validated


def test_bulk_multiple_signatures_validate_before_processing_and_retry(
    organization_client, db_session, template,
):
    from copy import deepcopy

    layout = signature_layout()
    first = layout["elements"][0]
    first["box"] = {"x": 0.02, "y": 0.1, "width": 0.45, "height": 0.7}
    second = deepcopy(first)
    second["id"] = "signature_2"
    second["box"]["x"] = 0.52
    layout["elements"].append(second)
    template.layout = layout
    template.positions = {}
    db_session.flush()
    batch = create_batch(organization_client, template, [
        signature_valid_row("MULTI-1", "First", 2),
        signature_valid_row("MULTI-2", "Second", 3),
    ])
    signatures = {"signature": signature_payload(), "signature_2": signature_payload(signer_name="ผู้ลงนามสอง")}
    missing = organization_client.post(f"/batches/{batch['id']}/process", json={
        "signatures": {"signature": signatures["signature"]},
    })
    assert missing.status_code == 422
    assert db_session.get(Batch, batch["id"]).status == BatchStatus.validated
    response = organization_client.post(f"/batches/{batch['id']}/process", json={"signatures": signatures})
    assert response.status_code == 200, response.text
    assert response.json()["success_count"] == 2
    assert db_session.scalar(select(func.count(Certificate.id))) == 2
    _force_row_failed(db_session, batch["id"])
    missing_retry = organization_client.post(f"/batches/{batch['id']}/retry", json={
        "signatures": {"signature_2": signatures["signature_2"]},
    })
    assert missing_retry.status_code == 422
    retried = organization_client.post(f"/batches/{batch['id']}/retry", json={"signatures": signatures})
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == BatchStatus.completed.value
    assert db_session.scalar(select(func.count(Certificate.id))) == 2


def test_bulk_prepares_once_and_shares_one_signature_object(
    organization_client, db_session, template, monkeypatch
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    batch = create_batch(
        organization_client,
        template,
        [
            signature_valid_row("SIG-1", "ผู้รับหนึ่ง", 2),
            signature_valid_row("SIG-2", "ผู้รับสอง", 3),
        ],
    )
    seen = []
    real_create = batch_service_module.create_certificate

    def track_create(db, actor, payload, key, prepared_signatures=None):
        seen.append(prepared_signatures)
        return real_create(db, actor, payload, key, prepared_signatures=prepared_signatures)

    monkeypatch.setattr(batch_service_module, "create_certificate", track_create)
    response = organization_client.post(
        f"/batches/{batch['id']}/process",
        json={"signature": signature_payload()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["success_count"] == 2
    assert len(seen) == 2 and seen[0] is seen[1]
    with pytest.raises(ValueError):
        seen[0]["signature"].image.getbbox()
    assert len(list((p for p in settings.CERTIFICATE_DIR.iterdir() if p.suffix in {".png", ".webp"}))) == 2


def _force_row_failed(db_session, batch_id):
    row = db_session.scalar(
        select(BatchRow).where(BatchRow.batch_id == batch_id).order_by(BatchRow.id)
    )
    row.status = "failed"
    row.error_code = "RETRY"
    row.error_message = "retry"
    row.batch.status = BatchStatus.partial
    row.batch.success_count = 0
    row.batch.failure_count = 1
    db_session.commit()
    return row.id


def test_bulk_retry_requires_a_fresh_signature_before_claiming_rows(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    batch = create_batch(
        organization_client, template, [signature_valid_row()]
    )
    first = organization_client.post(
        f"/batches/{batch['id']}/process",
        json={"signature": signature_payload()},
    )
    assert first.status_code == 200, first.text
    failed_row_id = _force_row_failed(db_session, batch["id"])

    missing = organization_client.post(f"/batches/{batch['id']}/retry")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "SIGNATURE_REQUIRED"
    assert db_session.get(BatchRow, failed_row_id).status == "failed"

    retried = organization_client.post(
        f"/batches/{batch['id']}/retry",
        json={"signature": signature_payload()},
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == BatchStatus.completed.value


def test_bulk_signature_text_can_extend_beyond_its_block(
    organization_client, db_session, template
):
    template.positions = {}
    template.layout = signature_layout(height=0.01)
    db_session.flush()
    batch = create_batch(
        organization_client, template, [signature_valid_row()]
    )
    response = organization_client.post(
        f"/batches/{batch['id']}/process",
        json={
            "signature": signature_payload(
                signer_name="ชื่อยาวมาก " * 15,
                signer_position="ตำแหน่งยาวมาก " * 15,
            )
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == BatchStatus.completed.value
    assert response.json()["rows"][0]["error_code"] is None
    assert db_session.scalar(select(func.count(Certificate.id))) == 1
    assert list((p for p in settings.CERTIFICATE_DIR.iterdir() if p.suffix in {".png", ".webp"}))


def test_bulk_closes_prepared_signature_when_row_loop_raises(
    db_session, organization_user, template, monkeypatch
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    row = signature_valid_row()
    batch = confirm_batch(
        db_session,
        organization_user,
        batch_payload(template, [row]),
        [row],
    )
    captured = {}
    real_prepare = batch_service_module.prepare_signatures_for_layout

    def track_prepare(layout, submissions, **kwargs):
        captured["prepared"] = real_prepare(layout, submissions, **kwargs)
        return captured["prepared"]

    monkeypatch.setattr(
        batch_service_module, "prepare_signatures_for_layout", track_prepare
    )
    monkeypatch.setattr(
        batch_service_module,
        "_run_rows",
        Mock(side_effect=RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError, match="stop"):
        process_batch(
            db_session,
            organization_user,
            batch.id,
            signature=signature_submission(),
        )
    with pytest.raises(ValueError):
        captured["prepared"]["signature"].image.getbbox()
