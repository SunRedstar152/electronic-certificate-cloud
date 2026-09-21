from __future__ import annotations

from dataclasses import asdict
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from auth import require_organization_user
from core.errors import api_error
from database import get_db
from models import User
from schemas import BatchCreate, BatchProcessRequest, BatchResponse
from services.archive_builder import build_batch_archive, iter_archive
from services.batch_service import (
    BatchNotFoundError,
    BatchActivityNotApprovedError,
    BatchStateError,
    BatchTemplateNotFoundError,
    confirm_batch,
    get_owned_active_template,
    get_owned_batch,
    list_owned_batches,
    preview_import,
    process_batch,
    retry_failed_rows,
)
from services.import_parser import ImportParseError, MAX_IMPORT_BYTES, parse_xlsx
from services.certificates import template_uses_legacy_layout
from services.template_layout import custom_field_specs
from services.signatures import SignatureError


router = APIRouter(prefix="/batches", tags=["Batches"])


class BatchConfirmationRow(BaseModel):
    row_number: int = Field(gt=0)
    recipient_reference: str | None = Field(default=None, max_length=255)
    recipient_name: str | None = Field(default=None, max_length=255)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    normalized_data: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    recipient_type: str = "INTERNAL"
    external_organization: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class BatchConfirmation(BatchCreate):
    rows: list[BatchConfirmationRow] = Field(min_length=1)


def _not_found():
    return api_error(404, "BATCH_NOT_FOUND", "Batch not found.")


def _template_not_found():
    return api_error(
        404,
        "BATCH_TEMPLATE_NOT_FOUND",
        "An active certificate template was not found.",
    )


def _invalid_import(error: ImportParseError):
    return api_error(400, error.code, error.message)


def _mapping(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ImportParseError(
            "IMPORT_INVALID_MAPPING", "The import column mapping is invalid."
        ) from error
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in parsed.items()
    ):
        raise ImportParseError(
            "IMPORT_INVALID_MAPPING", "The import column mapping is invalid."
        )
    return parsed


@router.post("/headers")
async def extract_headers(
    source_type: Annotated[Literal["xlsx"], Form()],
    file: Annotated[UploadFile | None, File()] = None,
    current_user: User = Depends(require_organization_user),
):
    try:
        if file is None:
            raise ImportParseError("IMPORT_INVALID_FILE", "An Excel (.xlsx) file is required.")
        table = parse_xlsx(await file.read(MAX_IMPORT_BYTES + 1))
        return {
            "headers": list(table.headers),
            "preview_rows": [row.values for row in table.rows[:5]]
        }
    except ImportParseError as error:
        raise _invalid_import(error)
    finally:
        if file is not None:
            await file.close()


@router.post("/preview")
async def preview_batch(
    template_id: Annotated[int, Form(gt=0)],
    source_type: Annotated[Literal["xlsx"], Form()],
    mapping: Annotated[str, Form()],
    constant_fields: Annotated[str, Form()] = "{}",
    file: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        template = get_owned_active_template(
            db, current_user.organization_id, template_id
        )
        if file is None:
            raise ImportParseError("IMPORT_INVALID_FILE", "An Excel (.xlsx) file is required.")
        source = await file.read(MAX_IMPORT_BYTES + 1)
        specs = (
            None
            if template_uses_legacy_layout(template)
            else custom_field_specs(template.layout)
        )
        table, rows = preview_import(
            source_type,
            source,
            _mapping(mapping),
            field_specs=specs,
            template=template,
            constants=_mapping(constant_fields),
        )
    except BatchTemplateNotFoundError:
        raise _template_not_found()
    except ImportParseError as error:
        raise _invalid_import(error)
    finally:
        if file is not None:
            await file.close()
    return {
        "headers": list(table.headers),
        "rows": [asdict(row) for row in rows],
    }


@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_batch(
    payload: BatchConfirmation,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        batch_data = payload.model_dump(exclude={"rows"})
        return confirm_batch(db, current_user, batch_data, payload.rows)
    except BatchTemplateNotFoundError:
        raise _template_not_found()
    except BatchActivityNotApprovedError:
        raise api_error(422, "BATCH_ACTIVITY_NOT_APPROVED", "Only approved activities may issue certificates.")
    except ImportParseError as error:
        raise _invalid_import(error)


@router.get("", response_model=list[BatchResponse])
def list_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    return list_owned_batches(db, current_user)


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        return get_owned_batch(db, current_user, batch_id)
    except BatchNotFoundError:
        raise _not_found()


def _processed_batch(db: Session, current_user: User, batch_id: int):
    try:
        return get_owned_batch(db, current_user, batch_id)
    except BatchNotFoundError:
        raise _not_found()


@router.post("/{batch_id}/process", response_model=BatchResponse)
def process_confirmed_batch(
    batch_id: int,
    payload: BatchProcessRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        process_batch(
            db,
            current_user,
            batch_id,
            signature=payload.signature if payload else None,
            signatures=payload.signatures if payload else None,
        )
    except SignatureError as error:
        raise api_error(422, error.code, error.public_message)
    except BatchNotFoundError:
        raise _not_found()
    except BatchStateError:
        raise api_error(409, "BATCH_INVALID_STATE", "The batch cannot be processed.")
    except BatchTemplateNotFoundError:
        raise _template_not_found()
    return _processed_batch(db, current_user, batch_id)


@router.post("/{batch_id}/retry", response_model=BatchResponse)
def retry_batch_failures(
    batch_id: int,
    payload: BatchProcessRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        retry_failed_rows(
            db,
            current_user,
            batch_id,
            signature=payload.signature if payload else None,
            signatures=payload.signatures if payload else None,
        )
    except SignatureError as error:
        raise api_error(422, error.code, error.public_message)
    except BatchNotFoundError:
        raise _not_found()
    except BatchStateError:
        raise api_error(409, "BATCH_INVALID_STATE", "The batch cannot be retried.")
    except BatchTemplateNotFoundError:
        raise _template_not_found()
    return _processed_batch(db, current_user, batch_id)


@router.get("/{batch_id}/download")
def download_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        download = build_batch_archive(db, current_user, batch_id)
    except BatchNotFoundError:
        raise _not_found()
    return StreamingResponse(
        iter_archive(download),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{download.filename}"'
        },
    )


__all__ = ["router"]
