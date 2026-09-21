from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from models import (
    Activity,
    ActivityStatus,
    Batch,
    BatchRow,
    BatchStatus,
    RecordStatus,
    Template,
    User,
    UserRole,
)
from schemas import BatchCreate, BatchSummary, CertificateCreate, SignatureSubmission
from services.audit import record_audit
from services.certificates import (
    CertificateFieldInvalidError,
    CertificateFontUnavailableError,
    CertificateQrInvalidError,
    CertificateRequiredFieldMissingError,
    CertificateTemplateNotFoundError,
    CertificateThaiShapingUnavailableError,
    CertificateTextOverflowError,
    ImageRenderError,
    create_certificate,
    normalize_template_certificate_fields,
)
from services.import_parser import (
    IMPORT_REQUIRED_VALUE,
    IMPORT_DUPLICATE_REFERENCE,
    IMPORT_VALUE_TOO_LONG,
    ImportRowError,
    ImportParseError,
    MAX_IMPORT_COLUMNS,
    MAX_IMPORT_ROWS,
    NormalizedRecipient,
    ParsedTable,
    map_rows,
    parse_xlsx,
)
from services.template_layout import CustomFieldSpec
from services.signatures import (
    PreparedSignature,
    SignatureTextOverflowError,
    prepare_signatures_for_layout,
    close_prepared_signatures,
)


_PENDING = "pending"
_PROCESSING = "processing"
_SUCCESS = "success"
_FAILED = "failed"
_CLAIM_LEASE = timedelta(minutes=15)


class BatchServiceError(RuntimeError):
    pass


class BatchNotFoundError(BatchServiceError):
    pass


class BatchTemplateNotFoundError(BatchServiceError):
    pass


class BatchActivityNotApprovedError(BatchServiceError):
    pass


class BatchStateError(BatchServiceError):
    pass


@dataclass(frozen=True)
class RowFailure:
    code: str
    message: str


class BatchRowFailure(BatchServiceError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _organization_id(actor: User) -> int:
    if actor.role != UserRole.organization or actor.organization_id is None:
        raise BatchServiceError("An organization user is required to manage batches.")
    return actor.organization_id


def preview_import(
    source_type: str,
    source: bytes,
    mapping: Mapping[str, str],
    *,
    field_specs: Iterable[CustomFieldSpec] | None = None,
    template: Template | None = None,
    constants: Mapping[str, str] | None = None,
) -> tuple[ParsedTable, list[NormalizedRecipient]]:
    normalized_type = source_type.strip().casefold()
    if normalized_type == "xlsx" and isinstance(source, bytes):
        table = parse_xlsx(source)
    else:
        raise ImportParseError(
            "IMPORT_INVALID_FILE",
            "Only Excel (.xlsx) imports are supported.",
        )
    rows = map_rows(table, mapping, field_specs=field_specs, constants=constants)
    if template is not None:
        rows = [_normalize_preview_recipient(row, template) for row in rows]
    return table, rows


def _field_error(error: Exception) -> ImportRowError:
    if isinstance(error, CertificateRequiredFieldMissingError):
        return ImportRowError(
            code="CERTIFICATE_REQUIRED_FIELD_MISSING",
            message="A required template field is missing.",
            field="custom_fields",
        )
    return ImportRowError(
        code="CERTIFICATE_FIELD_INVALID",
        message="A template field value is invalid.",
        field="custom_fields",
    )


def _normalize_preview_recipient(
    recipient: NormalizedRecipient,
    template: Template,
) -> NormalizedRecipient:
    errors = list(recipient.errors)
    try:
        custom_fields = normalize_template_certificate_fields(
            template, recipient.custom_fields
        )
    except (CertificateRequiredFieldMissingError, CertificateFieldInvalidError) as exc:
        custom_fields = recipient.custom_fields
        errors.append(_field_error(exc))
    return NormalizedRecipient(
        row_number=recipient.row_number,
        recipient_reference=recipient.recipient_reference,
        recipient_name=recipient.recipient_name,
        custom_fields=custom_fields,
        errors=tuple(errors),
    )


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_confirmed_rows(
    rows: Iterable[object],
    template: Template,
) -> list[NormalizedRecipient]:
    raw_rows = list(rows)
    if not raw_rows:
        raise ImportParseError(
            "IMPORT_ROW_REQUIRED", "At least one recipient row is required."
        )
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ImportParseError(
            "IMPORT_ROW_LIMIT_EXCEEDED",
            f"An import can contain at most {MAX_IMPORT_ROWS} data rows.",
        )

    row_numbers: set[int] = set()
    custom_headers: set[str] = set()
    prepared: list[tuple[int, str, str, dict[str, Any]]] = []
    for item in raw_rows:
        row_number = _value(item, "row_number")
        if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= 0:
            raise ImportParseError(
                "IMPORT_INVALID_ROW_NUMBER", "Import row numbers must be positive integers."
            )
        if row_number in row_numbers:
            raise ImportParseError(
                "IMPORT_DUPLICATE_ROW_NUMBER", "Import row numbers must be unique."
            )
        row_numbers.add(row_number)

        custom_fields = _value(item, "custom_fields")
        if custom_fields is None:
            custom_fields = _value(item, "normalized_data", {})
        if not isinstance(custom_fields, Mapping):
            raise ImportParseError(
                "IMPORT_INVALID_ROW", "Recipient custom fields must be an object."
            )
        normalized_custom: dict[str, Any] = {}
        for key, value in custom_fields.items():
            if not isinstance(key, str) or not key.strip():
                raise ImportParseError(
                    "IMPORT_INVALID_ROW", "Recipient custom field names cannot be empty."
                )
            normalized_key = key.strip()
            if normalized_key in {"recipient_reference", "recipient_name"}:
                raise ImportParseError(
                    "IMPORT_INVALID_ROW", "Recipient custom field names are invalid."
                )
            normalized_custom[normalized_key] = deepcopy(value)
            custom_headers.add(normalized_key)

        reference = _value(item, "recipient_reference")
        name = _value(item, "recipient_name")
        if reference is not None and not isinstance(reference, str):
            raise ImportParseError(
                "IMPORT_INVALID_ROW", "Recipient references must be text."
            )
        if name is not None and not isinstance(name, str):
            raise ImportParseError(
                "IMPORT_INVALID_ROW", "Recipient names must be text."
            )
        prepared.append((row_number, reference or "", name or "", normalized_custom))

    if len(custom_headers) + 2 > MAX_IMPORT_COLUMNS:
        raise ImportParseError(
            "IMPORT_COLUMN_LIMIT_EXCEEDED",
            f"An import can contain at most {MAX_IMPORT_COLUMNS} columns.",
        )

    recipients: list[NormalizedRecipient] = []
    seen_references: set[str] = set()
    for row_number, raw_reference, raw_name, custom_fields in prepared:
        reference, name = raw_reference.strip(), raw_name.strip()
        errors: list[ImportRowError] = []
        for field, value in (
            ("recipient_reference", reference),
            ("recipient_name", name),
        ):
            if not value:
                errors.append(
                    ImportRowError(
                        code=IMPORT_REQUIRED_VALUE,
                        message=f"The {field} value is required.",
                        field=field,
                    )
                )
            elif len(value) > 255:
                errors.append(
                    ImportRowError(
                        code=IMPORT_VALUE_TOO_LONG,
                        message=f"The {field} value must not exceed 255 characters.",
                        field=field,
                    )
                )
        if reference and len(reference) <= 255:
            if reference in seen_references:
                errors.append(
                    ImportRowError(
                        code=IMPORT_DUPLICATE_REFERENCE,
                        message="The recipient reference is duplicated in this import.",
                        field="recipient_reference",
                    )
                )
            else:
                seen_references.add(reference)
        try:
            normalized_custom = normalize_template_certificate_fields(
                template, custom_fields
            )
        except (CertificateRequiredFieldMissingError, CertificateFieldInvalidError) as exc:
            normalized_custom = deepcopy(custom_fields)
            errors.append(_field_error(exc))
        recipients.append(
            NormalizedRecipient(
                row_number=row_number,
                recipient_reference=reference or None,
                recipient_name=name or None,
                custom_fields=normalized_custom,
                errors=tuple(errors),
            )
        )
    return recipients


def _batch_payload(payload: BatchCreate | Mapping[str, Any]) -> BatchCreate:
    if isinstance(payload, BatchCreate):
        return payload
    return BatchCreate.model_validate(payload)


def get_owned_active_template(
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
        raise BatchTemplateNotFoundError("Template not found.")
    return template


def confirm_batch(
    db: Session,
    actor: User,
    payload: BatchCreate | Mapping[str, Any],
    rows: Iterable[object],
) -> Batch:
    organization_id = _organization_id(actor)
    batch_data = _batch_payload(payload)
    template = get_owned_active_template(db, organization_id, batch_data.template_id)
    if batch_data.activity_id is not None:
        activity = db.scalar(select(Activity).where(Activity.id == batch_data.activity_id, Activity.organization_id == organization_id))
        if activity is None or activity.approval_status is not ActivityStatus.APPROVED:
            raise BatchActivityNotApprovedError("Only approved activities may issue certificates.")
    normalized_rows = _normalize_confirmed_rows(rows, template)

    batch = Batch(
        organization_id=organization_id,
        template_id=batch_data.template_id,
        topic=batch_data.topic,
        issue_date=batch_data.issue_date,
        event_metadata=deepcopy(batch_data.event_metadata),
        source_type=batch_data.source_type,
        status=BatchStatus.draft,
        total_count=len(normalized_rows),
        success_count=0,
        failure_count=0,
        created_by_id=actor.id,
        activity_id=batch_data.activity_id,
        certificate_type_id=batch_data.certificate_type_id,
        recipient_type=batch_data.recipient_type,
        external_organization=batch_data.external_organization,
    )
    db.add(batch)
    try:
        db.flush()
        for recipient in normalized_rows:
            first_error = recipient.errors[0] if recipient.errors else None
            batch.rows.append(
                BatchRow(
                    row_number=recipient.row_number,
                    recipient_reference=recipient.recipient_reference,
                    recipient_name=recipient.recipient_name,
                    normalized_data=deepcopy(recipient.custom_fields),
                    status=_FAILED if first_error else _PENDING,
                    error_code=first_error.code if first_error else None,
                    error_message=first_error.message if first_error else None,
                    recipient_type=batch_data.recipient_type,
                    external_organization=batch_data.external_organization,
                )
            )
        batch.failure_count = sum(bool(row.error_code) for row in batch.rows)
        batch.status = BatchStatus.validated
        record_audit(
            db,
            actor,
            "batch.confirm",
            "batch",
            batch.id,
            {
                "template_id": batch.template_id,
                "source_type": batch.source_type,
                "total_count": batch.total_count,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_owned_batch(db, actor, batch.id)


def get_owned_batch(db: Session, actor: User, batch_id: int) -> Batch:
    organization_id = _organization_id(actor)
    batch = db.scalar(
        select(Batch)
        .where(Batch.id == batch_id, Batch.organization_id == organization_id)
        .options(selectinload(Batch.rows))
        .execution_options(populate_existing=True)
    )
    if batch is None:
        raise BatchNotFoundError("Batch not found.")
    batch.rows.sort(key=lambda row: (row.row_number, row.id))
    return batch


def list_owned_batches(db: Session, actor: User) -> list[Batch]:
    organization_id = _organization_id(actor)
    batches = list(
        db.scalars(
            select(Batch)
            .where(Batch.organization_id == organization_id)
            .options(selectinload(Batch.rows))
            .order_by(Batch.id.desc())
        )
    )
    for batch in batches:
        batch.rows.sort(key=lambda row: (row.row_number, row.id))
    return batches


def _claim_row(
    db: Session,
    actor: User,
    batch_id: int,
    eligible_status: str,
    after_id: int,
    recover_stale: bool,
) -> int | None:
    stale_before = datetime.now(timezone.utc) - _CLAIM_LEASE
    status_predicate = BatchRow.status == eligible_status
    if recover_stale:
        status_predicate = or_(
            status_predicate,
            and_(
                BatchRow.status == _PROCESSING,
                BatchRow.updated_at < stale_before,
            ),
        )
    row = db.scalar(
        select(BatchRow)
        .join(Batch, Batch.id == BatchRow.batch_id)
        .where(
            BatchRow.batch_id == batch_id,
            status_predicate,
            BatchRow.id > after_id,
            Batch.organization_id == actor.organization_id,
        )
        .order_by(BatchRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.commit()
        return None
    row.status = _PROCESSING
    row.updated_at = datetime.now(timezone.utc)
    row.error_code = None
    row.error_message = None
    row_id = row.id
    db.commit()
    return row_id


def _reload_row(db: Session, row_id: int) -> BatchRow:
    row = db.scalar(
        select(BatchRow)
        .where(BatchRow.id == row_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise BatchServiceError("The claimed batch row no longer exists.")
    return row


def _row_payload(db: Session, row_id: int) -> tuple[BatchRow, CertificateCreate]:
    row = db.scalar(
        select(BatchRow)
        .where(BatchRow.id == row_id)
        .options(selectinload(BatchRow.batch))
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise BatchServiceError("The claimed batch row no longer exists.")
    if not row.recipient_reference or not row.recipient_name:
        raise BatchRowFailure(
            IMPORT_REQUIRED_VALUE,
            "A recipient reference and recipient name are required.",
        )
    return row, CertificateCreate(
        batch_id=row.batch_id,
        template_id=row.batch.template_id,
        recipient_reference=row.recipient_reference,
        recipient_name=row.recipient_name,
        topic=row.batch.topic,
        issue_date=row.batch.issue_date,
        custom_fields=deepcopy(row.normalized_data),
        activity_id=row.batch.activity_id,
        certificate_type_id=row.batch.certificate_type_id,
        recipient_type=row.recipient_type,
        external_organization=row.external_organization,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _safe_row_failure(error: Exception) -> RowFailure:
    if isinstance(error, BatchRowFailure):
        return RowFailure(error.code, error.message)
    if isinstance(error, CertificateTemplateNotFoundError):
        return RowFailure(
            "CERTIFICATE_TEMPLATE_NOT_FOUND",
            "An active certificate template was not found.",
        )
    if isinstance(error, CertificateRequiredFieldMissingError):
        return RowFailure(
            "CERTIFICATE_REQUIRED_FIELD_MISSING",
            "A required template field is missing.",
        )
    if isinstance(error, CertificateFieldInvalidError):
        return RowFailure(
            "CERTIFICATE_FIELD_INVALID",
            "A template field value is invalid.",
        )
    if isinstance(error, CertificateTextOverflowError):
        return RowFailure(
            "CERTIFICATE_TEXT_OVERFLOW",
            "A field value does not fit its template area.",
        )
    if isinstance(error, SignatureTextOverflowError):
        return RowFailure(
            "SIGNATURE_TEXT_OVERFLOW",
            "ชื่อหรือตำแหน่งผู้ลงนามไม่พอดีกับกรอบลายเซ็น",
        )
    if isinstance(error, CertificateFontUnavailableError):
        return RowFailure(
            "CERTIFICATE_FONT_UNAVAILABLE",
            "A font required by the template is unavailable.",
        )
    if isinstance(error, CertificateQrInvalidError):
        return RowFailure(
            "CERTIFICATE_QR_INVALID",
            "A QR field in the template is too small to render safely.",
        )
    if isinstance(error, CertificateThaiShapingUnavailableError):
        return RowFailure(
            "CERTIFICATE_THAI_SHAPING_UNAVAILABLE",
            "Thai text rendering is not configured on this server.",
        )
    if isinstance(error, ImageRenderError):
        return RowFailure(
            "CERTIFICATE_RENDER_FAILED",
            "The certificate image could not be generated.",
        )
    if isinstance(error, IntegrityError) and _constraint_name(error) in {
        "uq_certificates_batch_recipient_reference",
        "uq_certificates_org_idempotency_key",
    }:
        return RowFailure(
            "CERTIFICATE_DUPLICATE",
            "A certificate already exists for this recipient in this batch.",
        )
    return RowFailure(
        "CERTIFICATE_ISSUANCE_FAILED",
        "The certificate could not be issued.",
    )


def _process_claimed_row(
    db: Session,
    actor: User,
    batch_id: int,
    row_id: int,
    signatures: dict[str, PreparedSignature] | None = None,
) -> None:
    try:
        row, payload = _row_payload(db, row_id)
        if not signatures:
            certificate = create_certificate(
                db,
                actor,
                payload,
                f"batch:{batch_id}:row:{row_id}",
            )
        else:
            certificate = create_certificate(
                db,
                actor,
                payload,
                f"batch:{batch_id}:row:{row_id}",
                prepared_signatures=signatures,
            )
        row = _reload_row(db, row_id)
        row.status = _SUCCESS
        row.error_code = None
        row.error_message = None
        row.certificate_id = certificate.id
        db.commit()
    except Exception as error:
        failure = _safe_row_failure(error)
        if not isinstance(error, SignatureTextOverflowError):
            db.rollback()
        row = _reload_row(db, row_id)
        row.status = _FAILED
        row.error_code = failure.code
        row.error_message = failure.message
        row.certificate_id = None
        db.commit()


def _finalize_batch(db: Session, actor: User, batch_id: int) -> BatchSummary:
    batch = db.scalar(
        select(Batch)
        .where(
            Batch.id == batch_id,
            Batch.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if batch is None:
        db.rollback()
        raise BatchNotFoundError("Batch not found.")

    counts = dict(
        db.execute(
            select(BatchRow.status, func.count(BatchRow.id))
            .where(BatchRow.batch_id == batch_id)
            .group_by(BatchRow.status)
        ).all()
    )
    batch.success_count = counts.get(_SUCCESS, 0)
    batch.failure_count = counts.get(_FAILED, 0)
    unfinished = batch.total_count - batch.success_count - batch.failure_count
    if unfinished > 0:
        batch.status = BatchStatus.processing
    elif batch.success_count == batch.total_count:
        batch.status = BatchStatus.completed
    elif batch.success_count == 0:
        batch.status = BatchStatus.failed
    else:
        batch.status = BatchStatus.partial
    db.commit()
    return BatchSummary.model_validate(batch)


def _prepare_processing(
    db: Session,
    actor: User,
    batch_id: int,
    retry: bool,
) -> Batch | None:
    batch = get_owned_batch(db, actor, batch_id)
    if retry:
        if batch.status == BatchStatus.completed:
            return None
        if batch.status not in {
            BatchStatus.partial,
            BatchStatus.failed,
            BatchStatus.processing,
        }:
            raise BatchStateError("Only failed batch rows can be retried.")
    else:
        if batch.status in {
            BatchStatus.completed,
            BatchStatus.partial,
            BatchStatus.failed,
        }:
            return None
        if batch.status not in {BatchStatus.validated, BatchStatus.processing}:
            raise BatchStateError("The batch is not ready for processing.")
    batch.status = BatchStatus.processing
    db.commit()
    return batch


def _run_rows(
    db: Session,
    actor: User,
    batch_id: int,
    eligible_status: str,
    recover_stale: bool,
    *,
    signatures: dict[str, PreparedSignature] | None = None,
) -> BatchSummary:
    last_claimed_id = 0
    while True:
        row_id = _claim_row(
            db,
            actor,
            batch_id,
            eligible_status,
            after_id=last_claimed_id,
            recover_stale=recover_stale,
        )
        if row_id is None:
            break
        last_claimed_id = row_id
        _process_claimed_row(db, actor, batch_id, row_id, signatures=signatures)
    return _finalize_batch(db, actor, batch_id)


def _process_or_retry(
    db: Session,
    actor: User,
    batch_id: int,
    *,
    retry: bool,
    signature_submission: SignatureSubmission | None,
    signature_submissions: dict[str, SignatureSubmission] | None,
) -> BatchSummary:
    batch = get_owned_batch(db, actor, batch_id)
    if retry and batch.status == BatchStatus.completed:
        return BatchSummary.model_validate(batch)
    if not retry and batch.status in {
        BatchStatus.completed,
        BatchStatus.partial,
        BatchStatus.failed,
    }:
        return BatchSummary.model_validate(batch)
    template = get_owned_active_template(db, actor.organization_id, batch.template_id)
    prepared = prepare_signatures_for_layout(
        template.layout, signature_submissions, single_submission=signature_submission,
    )
    try:
        if _prepare_processing(db, actor, batch_id, retry=retry) is None:
            return BatchSummary.model_validate(get_owned_batch(db, actor, batch_id))
        return _run_rows(
            db,
            actor,
            batch_id,
            _FAILED if retry else _PENDING,
            recover_stale=not retry,
            signatures=prepared,
        )
    finally:
        close_prepared_signatures(prepared)


def process_batch(
    db: Session,
    actor: User,
    batch_id: int,
    signature: SignatureSubmission | None = None,
    signatures: dict[str, SignatureSubmission] | None = None,
) -> BatchSummary:
    return _process_or_retry(
        db, actor, batch_id, retry=False, signature_submission=signature,
        signature_submissions=signatures,
    )


def retry_failed_rows(
    db: Session,
    actor: User,
    batch_id: int,
    signature: SignatureSubmission | None = None,
    signatures: dict[str, SignatureSubmission] | None = None,
) -> BatchSummary:
    return _process_or_retry(
        db, actor, batch_id, retry=True, signature_submission=signature,
        signature_submissions=signatures,
    )


__all__ = [
    "BatchNotFoundError",
    "BatchServiceError",
    "BatchStateError",
    "BatchTemplateNotFoundError",
    "confirm_batch",
    "get_owned_active_template",
    "get_owned_batch",
    "list_owned_batches",
    "preview_import",
    "process_batch",
    "retry_failed_rows",
]
