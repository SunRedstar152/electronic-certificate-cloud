from datetime import date
import base64
from io import BytesIO
from pathlib import Path
import threading
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import sessionmaker

from config import settings
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
from schemas import CertificateCreate, SignatureSubmission
import services.certificates as certificates_module
import services.image_builder as image_builder_module
from services.certificates import (
    CertificateTemplateNotFoundError,
    ImageRenderError,
    create_certificate,
    revoke_certificate,
)
from services.image_builder import render_certificate
from services.signatures import PreparedSignature


POSITIONS = {
    "name": {"x": 0.1, "y": 0.2},
    "topic": {"x": 0.1, "y": 0.4},
    "issue_date": {"x": 0.1, "y": 0.6},
    "qr": {"x": 0.7, "y": 0.2},
}


def test_thai_revocation_reason_is_preserved():
    assert certificates_module._normalize_revocation_reason(
        "  ออกเกียรติบัตรให้ผู้รับผิดคน  "
    ) == "ออกเกียรติบัตรให้ผู้รับผิดคน"


@pytest.fixture
def organization(db_session):
    value = Organization(name="Certificate Organization", code="CERT-SERVICE")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def other_organization(db_session):
    value = Organization(name="Other Certificate Organization", code="CERT-OTHER")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def org_user(db_session, organization):
    value = User(
        username="certificate-service-user",
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
    Image.new("RGB", (600, 400), "white").save(background)
    value = Template(
        organization_id=organization.id,
        name="Service Template",
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
    destination = tmp_path / "certificates"
    monkeypatch.setattr(settings, "CERTIFICATE_DIR", destination)
    return destination


def valid_payload(template, **overrides):
    data = {
        "template_id": template.id,
        "recipient_reference": "RECIPIENT-001",
        "recipient_name": "Ada Lovelace",
        "topic": "Secure Certificate Systems",
        "issue_date": date(2026, 8, 12),
        "custom_fields": {"course": "PKRU"},
    }
    data.update(overrides)
    return CertificateCreate(**data)


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


def signature_submission():
    image = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    try:
        image.putpixel((20, 25), (20, 20, 20, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
    finally:
        image.close()
    return SignatureSubmission(
        png_data_url=f"data:image/png;base64,{encoded}",
        signer_name="ผู้ช่วยศาสตราจารย์ ดร. เทิดศักดิ์ สุพันธ์ดี",
        signer_position="คณบดีคณะครุศาสตร์",
    )


def committed_context(db_session, tmp_path, label):
    engine = db_session.get_bind().engine
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    setup = session_factory()
    suffix = uuid4().hex
    background = tmp_path / f"{label}-{suffix}.png"
    Image.new("RGB", (600, 400), "white").save(background)
    organization = Organization(
        name=f"{label} Organization", code=f"{label[:8]}-{suffix[:12]}"
    )
    setup.add(organization)
    setup.flush()
    actor = User(
        username=f"{label.lower()}-{suffix}",
        password_hash="hash",
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    template = Template(
        organization_id=organization.id,
        name=f"{label} Template",
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 600, "height": 400},
        status=RecordStatus.active,
    )
    setup.add_all([actor, template])
    setup.commit()
    result = (session_factory, organization.id, actor.id, template.id)
    setup.close()
    return result


def cleanup_committed_context(session_factory, organization_id):
    cleanup = session_factory()
    paths = []
    try:
        paths = list(
            cleanup.scalars(
                select(Certificate.image_path).where(
                    Certificate.organization_id == organization_id
                )
            )
        )
        cleanup.execute(
            delete(AuditLog).where(AuditLog.organization_id == organization_id)
        )
        cleanup.execute(
            delete(Certificate).where(
                Certificate.organization_id == organization_id
            )
        )
        cleanup.execute(
            delete(Template).where(Template.organization_id == organization_id)
        )
        cleanup.execute(delete(User).where(User.organization_id == organization_id))
        cleanup.execute(
            delete(Organization).where(Organization.id == organization_id)
        )
        cleanup.commit()
    finally:
        cleanup.close()
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)


def test_render_certificate_draws_all_fields_and_atomically_publishes(
    template, org_user, tmp_path, monkeypatch
):
    certificate = Certificate(
        organization_id=org_user.organization_id,
        template_id=template.id,
        verification_code="RENDER123",
        recipient_reference="RECIPIENT-001",
        recipient_name="Ada Lovelace",
        topic="Secure Certificate Systems",
        issue_date=date(2026, 8, 12),
        created_by_id=org_user.id,
    )
    output = tmp_path / "rendered" / "certificate.png"
    captured = {}
    drawn_text = []

    real_draw = image_builder_module.ImageDraw.Draw

    class TrackingDraw:
        def __init__(self, image):
            self.delegate = real_draw(image)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def text(self, coordinates, value, **kwargs):
            drawn_text.append(value)
            return self.delegate.text(coordinates, value, **kwargs)

        def rectangle(self, coordinates, **kwargs):
            return self.delegate.rectangle(coordinates, **kwargs)

    class FakeQr:
        def convert(self, _mode):
            return Image.new("RGB", (64, 64), "black")

    def fake_make(value):
        captured["url"] = value
        return FakeQr()

    monkeypatch.setattr(
        "services.image_builder.ImageDraw.Draw", lambda image: TrackingDraw(image)
    )
    monkeypatch.setattr("services.image_builder.qrcode.make", fake_make)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://certificates.example/pkru")

    result = render_certificate(template, certificate, output)

    assert result == output
    assert output.exists()
    with Image.open(output) as rendered:
        assert rendered.size == (600, 400)
    assert captured["url"] == "https://certificates.example/pkru/?code=RENDER123"
    assert drawn_text == [
        "Ada Lovelace",
        "Secure Certificate Systems",
        "2026-08-12",
        "ID: RENDER123",
    ]
    assert list(output.parent.iterdir()) == [output]


def test_render_certificate_never_replaces_existing_destination(
    template, org_user, tmp_path
):
    certificate = Certificate(
        organization_id=org_user.organization_id,
        template_id=template.id,
        verification_code="NOOVERWRITE123",
        recipient_reference="RECIPIENT-NO-OVERWRITE",
        recipient_name="Existing Destination",
        topic="No overwrite",
        issue_date=date(2026, 8, 12),
        created_by_id=org_user.id,
    )
    output = tmp_path / "rendered" / "existing.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"winner image")

    with pytest.raises(ImageRenderError):
        render_certificate(template, certificate, output)

    assert output.read_bytes() == b"winner image"
    assert list(output.parent.iterdir()) == [output]


def test_render_failure_leaves_no_certificate(
    db_session, org_user, template, monkeypatch
):
    monkeypatch.setattr(
        "services.certificates.render_certificate",
        Mock(side_effect=ImageRenderError("render failed")),
    )

    with pytest.raises(ImageRenderError):
        create_certificate(
            db_session, org_user, valid_payload(template), "render-failure"
        )

    assert db_session.scalar(select(func.count(Certificate.id))) == 0
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0
    assert not settings.CERTIFICATE_DIR.exists()


def test_create_derives_organization_renders_and_audits(
    db_session, org_user, template
):
    certificate = create_certificate(
        db_session, org_user, valid_payload(template), "create-once"
    )

    assert certificate.organization_id == org_user.organization_id
    assert certificate.created_by_id == org_user.id
    assert certificate.status == CertificateStatus.active
    assert len(certificate.verification_code) == 16
    assert certificate.verification_code.isalnum()
    assert certificate.verification_code == certificate.verification_code.upper()
    assert any(character.isalpha() for character in certificate.verification_code)
    assert any(character.isdigit() for character in certificate.verification_code)
    assert Path(certificate.image_path).is_file()
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "certificate.create")
    )
    assert audit.actor_id == org_user.id
    assert audit.organization_id == org_user.organization_id
    assert audit.target_id == str(certificate.id)


def test_idempotency_key_is_scoped_to_organization_and_returns_original(
    db_session, org_user, template
):
    first = create_certificate(
        db_session, org_user, valid_payload(template), "same-request"
    )
    second = create_certificate(
        db_session,
        org_user,
        valid_payload(template, recipient_name="Changed Name"),
        "same-request",
    )

    assert second.id == first.id
    assert second.verification_code == first.verification_code
    assert second.recipient_name == "Ada Lovelace"
    assert db_session.scalar(select(func.count(Certificate.id))) == 1


def test_concurrent_same_idempotency_key_returns_single_winner(
    db_session, tmp_path
):
    session_factory, organization_id, actor_id, template_id = committed_context(
        db_session, tmp_path, "Concurrent"
    )
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def issue(name):
        session = session_factory()
        try:
            actor = session.get(User, actor_id)
            payload = CertificateCreate(
                template_id=template_id,
                recipient_reference=f"CONCURRENT-{name}",
                recipient_name=name,
                topic="Concurrent issuance",
                issue_date=date(2026, 8, 12),
            )
            barrier.wait(timeout=5)
            certificate = create_certificate(
                session, actor, payload, "concurrent-request"
            )
            results.append((certificate.id, certificate.verification_code))
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(target=issue, args=("First",)),
        threading.Thread(target=issue, args=("Second",)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert results[0] == results[1]
        verification = session_factory()
        try:
            assert verification.scalar(
                select(func.count(Certificate.id)).where(
                    Certificate.organization_id == organization_id,
                    Certificate.idempotency_key == "concurrent-request",
                )
            ) == 1
        finally:
            verification.close()
    finally:
        cleanup_committed_context(session_factory, organization_id)


@pytest.mark.parametrize("status", [RecordStatus.inactive])
def test_create_rejects_inactive_owned_template(
    db_session, org_user, template, status
):
    template.status = status
    db_session.flush()

    with pytest.raises(CertificateTemplateNotFoundError):
        create_certificate(
            db_session, org_user, valid_payload(template), "inactive-template"
        )

    assert db_session.scalar(select(func.count(Certificate.id))) == 0


def test_create_rejects_other_organization_template(
    db_session, org_user, other_organization, tmp_path
):
    background = tmp_path / "other-background.png"
    Image.new("RGB", (100, 100), "white").save(background)
    other_template = Template(
        organization_id=other_organization.id,
        name="Other Template",
        background_image_path=str(background),
        positions=POSITIONS,
        dimensions={"width": 100, "height": 100},
        status=RecordStatus.active,
    )
    db_session.add(other_template)
    db_session.flush()

    with pytest.raises(CertificateTemplateNotFoundError):
        create_certificate(
            db_session, org_user, valid_payload(other_template), "foreign-template"
        )


def test_database_failure_removes_rendered_image(
    db_session, org_user, template, monkeypatch
):
    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="database unavailable"):
        create_certificate(
            db_session, org_user, valid_payload(template), "database-failure"
        )

    assert settings.CERTIFICATE_DIR.exists()
    assert list(settings.CERTIFICATE_DIR.iterdir()) == []
    assert db_session.scalar(select(func.count(Certificate.id))) == 0
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0


def test_code_collision_before_render_does_not_unlink_existing_image(
    db_session, org_user, template, monkeypatch
):
    code = "EXISTINGCODE123456789"
    settings.CERTIFICATE_DIR.mkdir(parents=True, exist_ok=True)
    existing_image = settings.CERTIFICATE_DIR / f"{code}.png"
    existing_image.write_bytes(b"existing certificate image")
    existing = Certificate(
        organization_id=org_user.organization_id,
        template_id=template.id,
        verification_code=code,
        recipient_reference="EXISTING-REFERENCE",
        recipient_name="Existing Recipient",
        topic="Existing Topic",
        issue_date=date(2026, 8, 12),
        image_path=str(existing_image),
        created_by_id=org_user.id,
    )
    db_session.add(existing)
    db_session.flush()
    monkeypatch.setattr("services.certificates._verification_code", lambda _db: code)

    with pytest.raises(Exception):
        create_certificate(
            db_session,
            org_user,
            valid_payload(
                template,
                recipient_reference="NEW-REFERENCE",
                recipient_name="New Recipient",
            ),
            "collision-request",
        )

    assert existing_image.read_bytes() == b"existing certificate image"


def test_no_idempotency_code_collision_retries_without_returning_existing(
    db_session, tmp_path, monkeypatch
):
    session_factory, organization_id, actor_id, template_id = committed_context(
        db_session, tmp_path, "NoIdemCollision"
    )
    existing_code = "SAMEORGCOLLISION001"
    replacement_code = "DISTINCTREPLACEMENT01"
    settings.CERTIFICATE_DIR.mkdir(parents=True, exist_ok=True)
    existing_image = settings.CERTIFICATE_DIR / f"{existing_code}.png"
    existing_image.write_bytes(b"existing same-organization image")
    seed = session_factory()
    try:
        existing = Certificate(
            organization_id=organization_id,
            template_id=template_id,
            verification_code=existing_code,
            recipient_reference="EXISTING-SAME-ORG",
            recipient_name="Unrelated Existing Recipient",
            topic="Unrelated Existing Topic",
            issue_date=date(2026, 8, 12),
            image_path=str(existing_image),
            created_by_id=actor_id,
        )
        seed.add(existing)
        seed.commit()
        existing_id = existing.id
    finally:
        seed.close()
    codes = iter([existing_code, replacement_code])
    monkeypatch.setattr(
        "services.certificates._verification_code", lambda _db: next(codes)
    )
    issue_session = session_factory()
    try:
        actor = issue_session.get(User, actor_id)
        issued = create_certificate(
            issue_session,
            actor,
            CertificateCreate(
                template_id=template_id,
                recipient_reference="NEW-NO-IDEMPOTENCY",
                recipient_name="New Intended Recipient",
                topic="Secure Certificate Systems",
                issue_date=date(2026, 8, 12),
            ),
            None,
        )

        assert issued.id != existing_id
        assert issued.verification_code == replacement_code
        assert issued.recipient_name == "New Intended Recipient"
        verification = session_factory()
        try:
            assert verification.scalar(
                select(func.count(Certificate.id)).where(
                    Certificate.organization_id == organization_id
                )
            ) == 2
        finally:
            verification.close()
        assert existing_image.read_bytes() == b"existing same-organization image"
    finally:
        issue_session.close()
        cleanup_committed_context(session_factory, organization_id)


def test_loser_cleanup_cannot_remove_winner_image_after_reservation_release(
    db_session, tmp_path, monkeypatch
):
    session_factory, organization_id, actor_id, template_id = committed_context(
        db_session, tmp_path, "PathOwnership"
    )
    shared_code = "REUSEDRESERVATION001"
    reservation_released = threading.Event()
    winner_finished = threading.Event()
    rendered_destinations = []
    results = []
    errors = []
    destination_lock = threading.Lock()
    loser_session = session_factory()
    real_recover = certificates_module._recover_durable_certificate
    real_render = certificates_module.render_certificate

    def fail_before_commit():
        raise RuntimeError("loser database failure")

    def coordinated_recovery(db, *args, **kwargs):
        recovered = real_recover(db, *args, **kwargs)
        if db is loser_session:
            reservation_released.set()
            assert winner_finished.wait(timeout=10)
        return recovered

    def track_render(template, certificate, output_path):
        with destination_lock:
            rendered_destinations.append(Path(output_path))
        return real_render(template, certificate, output_path)

    monkeypatch.setattr(
        certificates_module, "_verification_code", lambda _db: shared_code
    )
    monkeypatch.setattr(
        certificates_module, "_recover_durable_certificate", coordinated_recovery
    )
    monkeypatch.setattr(certificates_module, "render_certificate", track_render)
    monkeypatch.setattr(loser_session, "commit", fail_before_commit)

    def lose():
        try:
            actor = loser_session.get(User, actor_id)
            create_certificate(
                loser_session,
                actor,
                CertificateCreate(
                    template_id=template_id,
                    recipient_reference="PATH-LOSER",
                    recipient_name="Losing Recipient",
                    topic="Path ownership",
                    issue_date=date(2026, 8, 12),
                ),
                None,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            loser_session.close()

    def win():
        session = session_factory()
        try:
            assert reservation_released.wait(timeout=10)
            actor = session.get(User, actor_id)
            certificate = create_certificate(
                session,
                actor,
                CertificateCreate(
                    template_id=template_id,
                    recipient_reference="PATH-WINNER",
                    recipient_name="Winning Recipient",
                    topic="Path ownership",
                    issue_date=date(2026, 8, 12),
                ),
                None,
            )
            results.append(certificate.image_path)
        except BaseException as exc:
            errors.append(exc)
        finally:
            winner_finished.set()
            session.close()

    threads = [threading.Thread(target=lose), threading.Thread(target=win)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert len(results) == 1
        assert len(errors) == 1
        assert str(errors[0]) == "loser database failure"
        winner_path = Path(results[0])
        assert winner_path.is_file()
        assert len(rendered_destinations) == 2
        assert len(set(rendered_destinations)) == 2
        loser_paths = [
            path for path in rendered_destinations if path != winner_path
        ]
        assert len(loser_paths) == 1
        assert not loser_paths[0].exists()
    finally:
        winner_finished.set()
        for thread in threads:
            thread.join(timeout=1)
        cleanup_committed_context(session_factory, organization_id)


def test_commit_that_persists_then_raises_recovers_durable_certificate(
    db_session, tmp_path, monkeypatch
):
    session_factory, organization_id, actor_id, template_id = committed_context(
        db_session, tmp_path, "Ambiguous"
    )
    session = session_factory()
    real_commit = session.commit

    def persist_then_raise():
        real_commit()
        raise RuntimeError("connection dropped after commit")

    monkeypatch.setattr(session, "commit", persist_then_raise)
    try:
        actor = session.get(User, actor_id)
        payload = CertificateCreate(
            template_id=template_id,
            recipient_reference="AMBIGUOUS-001",
            recipient_name="Durable Recipient",
            topic="Ambiguous commit",
            issue_date=date(2026, 8, 12),
        )

        certificate = create_certificate(
            session, actor, payload, "ambiguous-commit"
        )

        assert certificate.id is not None
        assert Path(certificate.image_path).is_file()
        verification = session_factory()
        try:
            durable = verification.scalar(
                select(Certificate).where(
                    Certificate.organization_id == organization_id,
                    Certificate.idempotency_key == "ambiguous-commit",
                )
            )
            assert durable is not None
            assert durable.id == certificate.id
        finally:
            verification.close()
    finally:
        session.close()
        cleanup_committed_context(session_factory, organization_id)


def test_second_revoke_preserves_first_revocation_history(
    db_session, org_user, template
):
    certificate = create_certificate(
        db_session, org_user, valid_payload(template), "revoke-once"
    )
    first = revoke_certificate(
        db_session, org_user, certificate.verification_code, "Invalid recipient"
    )
    original_history = (
        first.revoked_at,
        first.revoked_by_id,
        first.revocation_reason,
    )

    second = revoke_certificate(
        db_session, org_user, certificate.verification_code, "Different reason"
    )

    assert second.status == CertificateStatus.revoked
    assert (
        second.revoked_at,
        second.revoked_by_id,
        second.revocation_reason,
    ) == original_history
    assert db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "certificate.revoke"
        )
    ) == 1
    assert Path(second.image_path).is_file()


def test_revoke_locks_owned_certificate_row(
    db_session, org_user, template
):
    certificate = create_certificate(
        db_session, org_user, valid_payload(template), "revoke-lock"
    )
    statements = []
    engine = db_session.get_bind().engine

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        revoke_certificate(
            db_session, org_user, certificate.verification_code, "Invalid recipient"
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert any(
        "FOR UPDATE" in statement.upper()
        and "FROM CERTIFICATES" in statement.upper()
        for statement in statements
    )


def test_create_certificate_owns_transient_signature_and_passes_it_to_renderer(
    db_session, org_user, template, monkeypatch
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    observed = {}

    def fake_prepare(layout, submissions, *, single_submission):
        prepared = PreparedSignature(
            image=Image.new("RGBA", (20, 20), (0, 0, 0, 0)),
            signer_name=single_submission.signer_name,
            signer_position=single_submission.signer_position,
        )
        observed["prepared"] = prepared
        return {"signature": prepared}

    def fake_render(template, certificate, destination, *, signatures=None):
        observed["signature"] = signatures["signature"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 400), "white").save(destination)
        return destination

    monkeypatch.setattr(certificates_module, "prepare_signatures_for_layout", fake_prepare)
    monkeypatch.setattr(certificates_module, "render_certificate", fake_render)

    certificate = create_certificate(
        db_session,
        org_user,
        valid_payload(template, custom_fields={}),
        "signature-service",
        signature=signature_submission(),
    )

    assert certificate.image_path
    assert observed["signature"] is observed["prepared"]
    with pytest.raises(ValueError):
        observed["prepared"].image.getbbox()


def test_create_certificate_closes_transient_signature_after_render_failure(
    db_session, org_user, template, monkeypatch
):
    template.positions = {}
    template.layout = signature_layout()
    db_session.flush()
    observed = {}

    def fake_prepare(layout, submissions, *, single_submission):
        prepared = PreparedSignature(
            image=Image.new("RGBA", (20, 20), (0, 0, 0, 0)),
            signer_name=single_submission.signer_name,
            signer_position=single_submission.signer_position,
        )
        observed["prepared"] = prepared
        return {"signature": prepared}

    monkeypatch.setattr(certificates_module, "prepare_signatures_for_layout", fake_prepare)
    monkeypatch.setattr(
        certificates_module,
        "render_certificate",
        Mock(side_effect=ImageRenderError("render failed")),
    )

    with pytest.raises(ImageRenderError):
        create_certificate(
            db_session,
            org_user,
            valid_payload(template, custom_fields={}),
            "signature-service-failure",
            signature=signature_submission(),
        )

    with pytest.raises(ValueError):
        observed["prepared"].image.getbbox()


def test_concurrent_revocations_preserve_exactly_one_history(
    db_session, tmp_path
):
    session_factory, organization_id, actor_id, template_id = committed_context(
        db_session, tmp_path, "ConcurrentRevoke"
    )
    setup = session_factory()
    try:
        actor = setup.get(User, actor_id)
        certificate = create_certificate(
            setup,
            actor,
            CertificateCreate(
                template_id=template_id,
                recipient_reference="CONCURRENT-REVOKE-001",
                recipient_name="Concurrent Revoke Recipient",
                topic="Concurrent revocation",
                issue_date=date(2026, 8, 12),
            ),
            "concurrent-revoke-setup",
        )
        code = certificate.verification_code
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def revoke(reason):
        session = session_factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait(timeout=5)
            certificate = revoke_certificate(session, actor, code, reason)
            results.append(
                (
                    certificate.revocation_reason,
                    certificate.revoked_by_id,
                    certificate.revoked_at,
                )
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(target=revoke, args=("First valid reason",)),
        threading.Thread(target=revoke, args=("Second valid reason",)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert results[0] == results[1]
        assert results[0][0] in {"First valid reason", "Second valid reason"}
        verification = session_factory()
        try:
            assert verification.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.action == "certificate.revoke",
                )
            ) == 1
        finally:
            verification.close()
    finally:
        cleanup_committed_context(session_factory, organization_id)
