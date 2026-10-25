from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import secrets
import string
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from services.object_storage import reference, discard_file

from config import settings
from models import (
    AuditLog,
    Activity,
    ActivityStatus,
    Certificate,
    CertificateStatus,
    RecordStatus,
    Template,
    User,
    UserRole,
)
from schemas import CertificateCreate, SignatureSubmission
from services.audit import record_audit
from services.font_registry import FontNotFoundError
from services.image_builder import ImageRenderError, QrTooSmallError, render_certificate
from services.template_layout import (
    TemplateLayoutError,
    legacy_positions_to_layout,
    normalize_layout,
)
from services.text_layout import TextOverflowError, ThaiShapingUnavailableError
from services.signatures import (
    PreparedSignature,
    SignatureTextOverflowError,
    prepare_signatures_for_layout,
    close_prepared_signatures,
)


_CODE_LETTERS = string.ascii_uppercase
_CODE_DIGITS = string.digits
_CODE_ALPHABET = _CODE_LETTERS + _CODE_DIGITS
_CODE_LENGTH = 16
_MAX_CODE_ATTEMPTS = 100


class CertificateServiceError(RuntimeError):
    pass


class CertificateTemplateNotFoundError(CertificateServiceError):
    pass


class CertificateNotFoundError(CertificateServiceError):
    pass


class RevocationReasonError(CertificateServiceError):
    pass


class CertificateNotRevokedError(CertificateServiceError):
    pass


class IdempotencyKeyError(CertificateServiceError):
    pass


class CertificateRequiredFieldMissingError(CertificateServiceError):
    pass


class CertificateFieldInvalidError(CertificateServiceError):
    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason
        self.field_key: str | None = None
        self.field_label: str | None = None


class CertificateTextOverflowError(CertificateServiceError):
    pass


class CertificateFontUnavailableError(CertificateServiceError):
    pass


class CertificateQrInvalidError(CertificateServiceError):
    pass


class CertificateThaiShapingUnavailableError(CertificateServiceError):
    pass


class CertificateActivityNotApprovedError(CertificateServiceError):
    pass


_MAX_CUSTOM_TEXT_LENGTH = 2_000
_MAX_CUSTOM_PAYLOAD_BYTES = 64 * 1024


def _organization_id(actor: User) -> int:
    if actor.role != UserRole.organization or actor.organization_id is None:
        raise CertificateServiceError(
            "An organization user is required to manage certificates."
        )
    return actor.organization_id


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise IdempotencyKeyError("The idempotency key is invalid.")
    return normalized


def _normalize_revocation_reason(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise RevocationReasonError("A revocation reason is required.")
    return normalized


def _normalized_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise CertificateFieldInvalidError("A custom date value is invalid.", reason="date_invalid") from exc
    raise CertificateFieldInvalidError("A custom date value is invalid.", reason="date_invalid")


def _normalized_time(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise CertificateFieldInvalidError("A custom time value is invalid.", reason="time_invalid") from exc
    else:
        raise CertificateFieldInvalidError("A custom time value is invalid.", reason="time_invalid")
    if parsed.utcoffset() is not None:
        raise CertificateFieldInvalidError("Custom time values cannot include a timezone.", reason="time_invalid")
    return parsed.isoformat(timespec="seconds" if parsed.second else "minutes")


def _normalized_range(value: Any, kind: str) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"start", "end"}
        or value["start"] in (None, "")
        or value["end"] in (None, "")
    ):
        raise CertificateFieldInvalidError("A custom range requires start and end values.", reason="range_incomplete")
    normalizer = _normalized_date if kind == "date_range" else _normalized_time
    start, end = normalizer(value["start"]), normalizer(value["end"])
    if start > end:
        raise CertificateFieldInvalidError("A custom range start cannot be after its end.", reason="range_reversed")
    return {"start": start, "end": end}


def _payload_size(value: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CertificateFieldInvalidError("Custom fields must be JSON values.") from exc
    if len(encoded) > _MAX_CUSTOM_PAYLOAD_BYTES:
        raise CertificateFieldInvalidError("Custom fields exceed the 64 KB limit.", reason="payload_too_large")


def normalize_certificate_fields(
    layout: object,
    custom_fields: object,
) -> dict[str, Any]:
    if not isinstance(custom_fields, dict):
        raise CertificateFieldInvalidError("Custom fields must be an object.")
    try:
        parsed = normalize_layout(layout)
    except TemplateLayoutError as exc:
        raise CertificateFieldInvalidError("The template layout is invalid.", reason="layout_invalid") from exc
    elements = {
        element["source"].removeprefix("custom."): element
        for element in parsed["elements"]
        if element.get("visible", True)
        and element["source"].startswith("custom.")
    }
    if not set(custom_fields).issubset(elements):
        raise CertificateFieldInvalidError("A custom field is not defined by the template.", reason="unknown_field")

    normalized: dict[str, Any] = {}
    for key, element in elements.items():
        raw = custom_fields.get(key)
        if isinstance(raw, str):
            raw = raw.strip()
        if raw is None or raw == "":
            if element.get("required", False):
                raise CertificateRequiredFieldMissingError(
                    f'The required field "{key}" is missing.'
                )
            continue

        kind = element["kind"]
        try:
            if kind in {"text", "multiline"}:
                if not isinstance(raw, str) or len(raw) > _MAX_CUSTOM_TEXT_LENGTH:
                    raise CertificateFieldInvalidError(
                        f'The custom field "{key}" must contain at most 2,000 characters.',
                        reason="text_invalid",
                    )
                value: Any = raw
            elif kind == "date":
                value = _normalized_date(raw)
            elif kind == "time":
                value = _normalized_time(raw)
            elif kind in {"date_range", "time_range"}:
                value = _normalized_range(raw, kind)
            else:
                raise CertificateFieldInvalidError(
                    f'The custom field "{key}" has an unsupported type.'
                )
        except CertificateFieldInvalidError as exc:
            exc.field_key = key
            exc.field_label = element["label"]
            raise
        normalized[key] = value
    _payload_size(normalized)
    return normalized


def _normalize_legacy_fields(custom_fields: object) -> dict[str, Any]:
    if not isinstance(custom_fields, dict):
        raise CertificateFieldInvalidError("Custom fields must be an object.")
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in custom_fields.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise CertificateFieldInvalidError("Custom field names are invalid.")
        key = raw_key.strip()
        value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if value == "":
            continue
        if isinstance(value, str) and len(value) > _MAX_CUSTOM_TEXT_LENGTH:
            raise CertificateFieldInvalidError("A custom field is too long.")
        normalized[key] = value
    _payload_size(normalized)
    return normalized


def _uses_legacy_layout(template: Template) -> bool:
    if not isinstance(template.layout, dict) or template.layout.get("version") != 2:
        return True
    try:
        legacy = legacy_positions_to_layout(template.positions, template.dimensions)
    except TemplateLayoutError:
        return False
    return normalize_layout(template.layout) == legacy


def _normalized_custom_fields(template: Template, custom_fields: object) -> dict[str, Any]:
    if _uses_legacy_layout(template):
        return _normalize_legacy_fields(custom_fields)
    return normalize_certificate_fields(template.layout, custom_fields)


def template_uses_legacy_layout(template: Template) -> bool:
    return _uses_legacy_layout(template)


def normalize_template_certificate_fields(
    template: Template,
    custom_fields: object,
) -> dict[str, Any]:
    return _normalized_custom_fields(template, custom_fields)


def _recipient_reference(payload: CertificateCreate) -> str:
    supplied = (payload.recipient_reference or "").strip()
    if supplied:
        return supplied
    if payload.batch_id is not None:
        raise CertificateFieldInvalidError(
            "Batch certificates require a recipient reference."
        )
    return str(uuid4())


def _verification_code(db: Session) -> str:
    for _ in range(_MAX_CODE_ATTEMPTS):
        characters = [
            secrets.choice(_CODE_LETTERS),
            secrets.choice(_CODE_DIGITS),
            *(
                secrets.choice(_CODE_ALPHABET)
                for _ in range(_CODE_LENGTH - 2)
            ),
        ]
        secrets.SystemRandom().shuffle(characters)
        code = "".join(characters)
        exists = db.scalar(
            select(Certificate.id).where(Certificate.verification_code == code)
        )
        if exists is None:
            return code
    raise CertificateServiceError("A unique certificate code could not be reserved.")


def _active_owned_template(
    db: Session, organization_id: int, template_id: int
) -> Template:
    template = db.scalar(
        select(Template).where(
            Template.id == template_id,
            Template.organization_id == organization_id,
            Template.status == RecordStatus.active,
            Template.deleted_at.is_(None),
        )
    )
    if template is None:
        raise CertificateTemplateNotFoundError("Template not found.")
    return template


def _approved_activity(db: Session, organization_id: int, activity_id: int | None) -> Activity | None:
    if activity_id is None:
        return None
    activity = db.scalar(select(Activity).where(Activity.id == activity_id, Activity.organization_id == organization_id))
    if activity is None or activity.approval_status is not ActivityStatus.APPROVED:
        raise CertificateActivityNotApprovedError("Only approved activities may issue certificates.")
    return activity


def _new_certificate(
    actor: User,
    organization_id: int,
    template: Template,
    payload: CertificateCreate,
    idempotency_key: str | None,
    verification_code: str,
) -> Certificate:
    return Certificate(
        organization_id=organization_id,
        batch_id=payload.batch_id,
        template_id=template.id,
        verification_code=verification_code,
        recipient_reference=_recipient_reference(payload),
        recipient_name=payload.recipient_name,
        topic=payload.topic,
        custom_fields=payload.custom_fields,
        issue_date=payload.issue_date,
        status=CertificateStatus.active,
        idempotency_key=idempotency_key,
        created_by_id=actor.id,
        activity_id=payload.activity_id,
        certificate_type_id=payload.certificate_type_id,
        recipient_type=payload.recipient_type,
        external_organization=payload.external_organization,
    )


def _is_verification_code_collision(error: IntegrityError) -> bool:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return (
        getattr(diagnostic, "constraint_name", None)
        == "uq_certificates_verification_code"
    )


def _reserve_certificate(
    db: Session,
    actor: User,
    organization_id: int,
    template: Template,
    payload: CertificateCreate,
    idempotency_key: str | None,
) -> Certificate:
    for _ in range(_MAX_CODE_ATTEMPTS):
        certificate = _new_certificate(
            actor,
            organization_id,
            template,
            payload,
            idempotency_key,
            _verification_code(db),
        )
        try:
            with db.begin_nested():
                db.add(certificate)
                db.flush()
        except IntegrityError as exc:
            if _is_verification_code_collision(exc):
                continue
            raise
        return certificate
    raise CertificateServiceError("A unique certificate code could not be reserved.")


def _acquire_idempotency_lock(
    db: Session, organization_id: int, idempotency_key: str
) -> None:
    digest = hashlib.blake2b(
        f"{organization_id}\0{idempotency_key}".encode("utf-8"),
        digest_size=8,
    ).digest()
    lock_id = int.from_bytes(digest, byteorder="big", signed=True)
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def _certificate_for_idempotency_key(
    db: Session, organization_id: int, idempotency_key: str
) -> Certificate | None:
    return db.scalar(
        select(Certificate).where(
            Certificate.organization_id == organization_id,
            Certificate.idempotency_key == idempotency_key,
        )
    )


def _recover_durable_certificate(
    db: Session,
    organization_id: int,
    idempotency_key: str | None,
    certificate_id: int | None,
    verification_code: str,
    commit_attempted: bool,
) -> tuple[bool, Certificate | None]:
    try:
        db.rollback()
    except Exception:
        return False, None

    try:
        if idempotency_key is not None:
            certificate = _certificate_for_idempotency_key(
                db, organization_id, idempotency_key
            )
        elif (
            not commit_attempted
            or certificate_id is None
            or not verification_code
        ):
            certificate = None
        else:
            certificate = db.scalar(
                select(Certificate).where(
                    Certificate.id == certificate_id,
                    Certificate.organization_id == organization_id,
                    Certificate.verification_code == verification_code,
                )
            )
    except Exception:
        return False, None
    return True, certificate


def _same_path(left: str | Path | None, right: Path) -> bool:
    if left is None:
        return False
    return str(left) == reference(right) or Path(left).resolve() == right.resolve()


def _recover_or_reraise(
    db: Session,
    error: Exception,
    organization_id: int,
    idempotency_key: str | None,
    certificate_id: int | None,
    verification_code: str,
    commit_attempted: bool,
    published_path: Path | None,
) -> Certificate:
    recovery_completed, durable = _recover_durable_certificate(
        db,
        organization_id,
        idempotency_key,
        certificate_id,
        verification_code,
        commit_attempted,
    )
    if durable is not None:
        if published_path is not None and not _same_path(
            durable.image_path, published_path
        ):
            discard_file(published_path)
        return durable
    if recovery_completed and published_path is not None:
        discard_file(published_path)
    raise error


def create_certificate(
    db: Session,
    actor: User,
    payload: CertificateCreate,
    idempotency_key: str | None,
    *,
    signature: SignatureSubmission | None = None,
    signatures: dict[str, SignatureSubmission] | None = None,
    prepared_signatures: dict[str, PreparedSignature] | None = None,
) -> Certificate:
    organization_id = _organization_id(actor)
    normalized_key = _normalize_idempotency_key(idempotency_key)
    code = ""
    certificate_id: int | None = None
    commit_attempted = False
    published_path: Path | None = None
    owns_prepared_signatures = prepared_signatures is None
    certificate: Certificate | None = None
    try:
        try:
            if normalized_key is not None:
                _acquire_idempotency_lock(db, organization_id, normalized_key)
                deleted = db.scalar(select(AuditLog.id).where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.action == "certificate.delete",
                    AuditLog.details["idempotency_digest"].astext == hashlib.sha256(normalized_key.encode()).hexdigest(),
                ).limit(1))
                if deleted is not None:
                    raise IdempotencyKeyError("This issuance was permanently deleted; use a new key.")
                existing = _certificate_for_idempotency_key(
                    db, organization_id, normalized_key
                )
                if existing is not None:
                    code = existing.verification_code
                    certificate_id = existing.id
                    commit_attempted = True
                    db.commit()
                    db.refresh(existing)
                    return existing

            template = _active_owned_template(db, organization_id, payload.template_id)
            _approved_activity(db, organization_id, payload.activity_id)
            if owns_prepared_signatures:
                prepared_signatures = prepare_signatures_for_layout(
                    template.layout, signatures, single_submission=signature,
                )
            payload = payload.model_copy(
                update={
                    "custom_fields": _normalized_custom_fields(
                        template, payload.custom_fields
                    )
                }
            )
            certificate = _reserve_certificate(
                db,
                actor,
                organization_id,
                template,
                payload,
                normalized_key,
            )
            code = certificate.verification_code
            certificate_id = certificate.id
            if certificate_id is None:
                raise CertificateServiceError(
                    "The certificate row could not be reserved."
                )
            destination = settings.CERTIFICATE_DIR / f"{code}-{certificate_id}.{settings.CERTIFICATE_STORAGE_FORMAT}"
            try:
                if not prepared_signatures:
                    rendered_path = render_certificate(template, certificate, destination)
                else:
                    rendered_path = render_certificate(
                        template,
                        certificate,
                        destination,
                        signatures=prepared_signatures,
                    )
            except ImageRenderError as exc:
                if isinstance(exc.__cause__, SignatureTextOverflowError):
                    raise exc.__cause__ from exc
                if isinstance(exc.__cause__, TextOverflowError):
                    raise CertificateTextOverflowError(str(exc.__cause__)) from exc
                if isinstance(exc.__cause__, FontNotFoundError):
                    raise CertificateFontUnavailableError(str(exc.__cause__)) from exc
                if isinstance(exc.__cause__, QrTooSmallError):
                    raise CertificateQrInvalidError(str(exc.__cause__)) from exc
                if isinstance(exc.__cause__, ThaiShapingUnavailableError):
                    raise CertificateThaiShapingUnavailableError(str(exc.__cause__)) from exc
                raise
            published_path = Path(rendered_path)
            certificate.image_path = reference(published_path)
            record_audit(
                db,
                actor,
                "certificate.create",
                "certificate",
                certificate.id,
                {
                    "verification_code": certificate.verification_code,
                    "template_id": template.id,
                },
            )
            commit_attempted = True
            db.commit()
        except Exception as exc:
            # Bulk processing supplies an already prepared in-memory signature.
            # Keep that row transaction alive for the batch worker to record a
            # row-level text overflow instead of rolling back its committed claim.
            if isinstance(exc, SignatureTextOverflowError) and not owns_prepared_signatures:
                if published_path is not None:
                    discard_file(published_path)
                if certificate is not None:
                    db.delete(certificate)
                    db.flush()
                raise
            return _recover_or_reraise(
                db,
                exc,
                organization_id,
                normalized_key,
                certificate_id,
                code,
                commit_attempted,
                published_path,
            )

        try:
            db.refresh(certificate)
        except Exception as exc:
            return _recover_or_reraise(
                db,
                exc,
                organization_id,
                normalized_key,
                certificate_id,
                code,
                True,
                published_path,
            )
        return certificate
    finally:
        if owns_prepared_signatures and prepared_signatures is not None:
            close_prepared_signatures(prepared_signatures)


def get_owned_certificate(db: Session, actor: User, code: str) -> Certificate:
    organization_id = _organization_id(actor)
    certificate = db.scalar(
        select(Certificate).where(
            Certificate.organization_id == organization_id,
            Certificate.verification_code == code.strip().upper(),
        )
    )
    if certificate is None:
        raise CertificateNotFoundError("Certificate not found.")
    return certificate


def revoke_certificate(
    db: Session, actor: User, code: str, reason: str
) -> Certificate:
    try:
        normalized_reason = _normalize_revocation_reason(reason)

        organization_id = _organization_id(actor)
        certificate = db.scalar(
            select(Certificate)
            .where(
                Certificate.organization_id == organization_id,
                Certificate.verification_code == code.strip().upper(),
            )
            .with_for_update()
        )
        if certificate is None:
            raise CertificateNotFoundError("Certificate not found.")
        if certificate.status == CertificateStatus.revoked:
            db.commit()
            db.refresh(certificate)
            return certificate

        certificate.status = CertificateStatus.revoked
        certificate.revoked_at = datetime.now(timezone.utc)
        certificate.revocation_reason = normalized_reason
        certificate.revoked_by_id = actor.id
        record_audit(
            db,
            actor,
            "certificate.revoke",
            "certificate",
            certificate.id,
            {
                "verification_code": certificate.verification_code,
                "reason": normalized_reason,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(certificate)
    return certificate


def delete_certificate(db: Session, actor: User, code: str) -> None:
    """Delete only a revoked, owned certificate; retain audit and batch history.

    Storage removal happens first: failure leaves a retryable revoked record.
    If the DB commit fails afterwards, retry tolerates an already missing image.
    """
    from services.object_storage import PREFIX, reference_path
    from services.storage_paths import resolve_certificate_file, StoredFileUnavailableError

    try:
        certificate = db.scalar(select(Certificate).where(
            Certificate.organization_id == _organization_id(actor),
            Certificate.verification_code == code.strip().upper(),
        ).with_for_update())
        if certificate is None:
            raise CertificateNotFoundError("Certificate not found.")
        if certificate.status != CertificateStatus.revoked:
            raise CertificateNotRevokedError("Revoke the certificate before deleting it.")
        if certificate.idempotency_key:
            _acquire_idempotency_lock(db, certificate.organization_id, certificate.idempotency_key)
        if certificate.image_path:
            if certificate.image_path.startswith(PREFIX):
                path = reference_path(certificate.image_path, settings.CERTIFICATE_DIR)
            else:
                try:
                    path = resolve_certificate_file(certificate.image_path)
                except StoredFileUnavailableError as error:
                    if error.reason != "missing":
                        raise
                    path = None
            if path is not None:
                discard_file(path, strict=True)
        record_audit(db, actor, "certificate.delete", "certificate", certificate.id, {
            "verification_code": certificate.verification_code,
            "batch_id": certificate.batch_id,
            "idempotency_digest": hashlib.sha256(certificate.idempotency_key.encode()).hexdigest() if certificate.idempotency_key else None,
        })
        if certificate.batch_row is not None:
            certificate.batch_row.certificate = None
            db.flush()
        db.delete(certificate)
        db.commit()
    except Exception:
        db.rollback()
        raise


__all__ = [
    "delete_certificate",
    "CertificateNotRevokedError",
    "CertificateNotFoundError",
    "CertificateQrInvalidError",
    "CertificateServiceError",
    "CertificateTemplateNotFoundError",
    "CertificateThaiShapingUnavailableError",
    "IdempotencyKeyError",
    "ImageRenderError",
    "RevocationReasonError",
    "create_certificate",
    "get_owned_certificate",
    "render_certificate",
    "revoke_certificate",
]
