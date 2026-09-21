import json
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.orm import Session
from services.object_storage import reference, discard_file

from auth import require_organization_user
from core.errors import api_error
from database import get_db
from models import RecordStatus, Template, User
from repositories.scoping import owned_query
from schemas import TemplateResponse, TemplateUpdate
from schemas.template_layout import TemplateLayout
from services.audit import record_audit
from services.builtin_templates import ensure_original_template
from services.font_registry import FontNotFoundError, resolve_font
from services.image_builder import MIN_QR_SIDE_PX, QrTooSmallError, qr_code_side, render_layout
from services.text_layout import TextOverflowError, ThaiShapingUnavailableError
from services.signatures import (
    SignatureTextOverflowError,
    signature_element_ids,
    close_prepared_signatures,
    sample_signature,
)
from services.template_layout import (
    TemplateLayoutError,
    legacy_positions_to_layout,
    normalize_layout,
)
from services.template_files import (
    MAX_TEMPLATE_IMAGE_BYTES,
    TemplateImageError,
    store_template_image,
    validate_template_image,
)
from services.storage_paths import StoredFileUnavailableError, resolve_template_file


router = APIRouter(prefix="/templates", tags=["Templates"])

ALLOWED_POSITION_KEYS = {
    "recipient_name",
    "topic",
    "issue_date",
    "qr_code",
}


def _template_not_found():
    return api_error(404, "TEMPLATE_NOT_FOUND", "Template not found.")


def _invalid_positions():
    return api_error(
        400,
        "TEMPLATE_INVALID_POSITIONS",
        "Template positions are invalid.",
    )


def _invalid_request():
    return api_error(
        422,
        "REQUEST_VALIDATION_ERROR",
        "The request contains invalid or missing fields.",
    )


def _invalid_update():
    return api_error(400, "TEMPLATE_INVALID_UPDATE", "Template update is invalid.")


def _invalid_layout():
    return api_error(
        400,
        "TEMPLATE_INVALID_LAYOUT",
        "The template layout is invalid.",
    )


def _font_not_found():
    return api_error(
        400,
        "TEMPLATE_FONT_NOT_FOUND",
        "A font referenced by the template is unavailable.",
    )


def _field_source_immutable():
    return api_error(
        409,
        "TEMPLATE_FIELD_SOURCE_IMMUTABLE",
        "An existing template field source cannot be changed.",
    )


def _parse_positions(value: str) -> dict[str, dict[str, float]]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        raise _invalid_positions()
    return _validate_positions(parsed)


def _validate_positions(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict) or not set(value).issubset(ALLOWED_POSITION_KEYS):
        raise _invalid_positions()

    normalized: dict[str, dict[str, float]] = {}
    for key, coordinates in value.items():
        if not isinstance(coordinates, dict) or set(coordinates) != {"x", "y"}:
            raise _invalid_positions()
        x = coordinates["x"]
        y = coordinates["y"]
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not 0.0 <= x <= 1.0
            or not 0.0 <= y <= 1.0
        ):
            raise _invalid_positions()
        normalized[key] = {"x": float(x), "y": float(y)}
    return normalized


def _parse_layout(value: str | dict) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return normalize_layout(parsed)
    except (json.JSONDecodeError, TypeError, TemplateLayoutError):
        raise _invalid_layout()


def _validate_layout_fonts(
    layout: dict,
    db: Session,
    organization_id: int,
) -> None:
    parsed = TemplateLayout.model_validate(layout)
    try:
        for element in parsed.elements:
            if element.style is not None:
                resolve_font(
                    db,
                    organization_id,
                    element.style.font,
                    weight=element.style.weight,
                )
    except FontNotFoundError as exc:
        raise _font_not_found() from exc


def _validate_immutable_sources(existing_layout: object, proposed_layout: dict) -> None:
    if not isinstance(existing_layout, dict) or existing_layout.get("version") != 2:
        return
    existing = {
        element.get("id"): element.get("source")
        for element in existing_layout.get("elements", [])
        if isinstance(element, dict)
    }
    proposed = {
        element.get("id"): element.get("source")
        for element in proposed_layout.get("elements", [])
        if isinstance(element, dict)
    }
    if any(
        element_id in proposed and proposed[element_id] != source
        for element_id, source in existing.items()
    ):
        raise _field_source_immutable()


def _validate_layout_geometry(layout: dict, dimensions: dict) -> None:
    width = int(dimensions.get("width", 0))
    height = int(dimensions.get("height", 0))
    parsed = TemplateLayout.model_validate(layout)
    for element in parsed.elements:
        if element.kind != "qr":
            continue
        box_width = max(1, round((element.box.x + element.box.width) * width) - round(element.box.x * width))
        box_height = max(1, round((element.box.y + element.box.height) * height) - round(element.box.y * height))
        side = qr_code_side(box_width, box_height)
        if side < MIN_QR_SIDE_PX:
            raise api_error(
                400,
                "TEMPLATE_QR_TOO_SMALL",
                str(QrTooSmallError(element.id)),
                element_id=element.id,
            )


def _parse_sample_values(value: str) -> dict:
    if len(value.encode("utf-8")) > 64 * 1024:
        raise api_error(
            400,
            "TEMPLATE_INVALID_SAMPLE_VALUES",
            "Template sample values are invalid.",
        )
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        raise api_error(
            400,
            "TEMPLATE_INVALID_SAMPLE_VALUES",
            "Template sample values are invalid.",
        )
    return parsed


def _owned_template(
    template_id: int, current_user: User, db: Session, *, include_deleted: bool = False
) -> Template:
    statement = owned_query(Template, current_user, db).where(
        Template.id == template_id
    )
    if not include_deleted:
        statement = statement.where(Template.deleted_at.is_(None))
    template = db.scalar(statement)
    if template is None:
        raise _template_not_found()
    return template


def _read_upload_data(file: UploadFile) -> bytes:
    return file.file.read(MAX_TEMPLATE_IMAGE_BYTES + 1)


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    name: Annotated[str, Form(min_length=1, max_length=255)],
    file: Annotated[UploadFile, File()],
    positions: Annotated[str | None, Form()] = None,
    layout: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    name = name.strip()
    if not name:
        raise _invalid_request()
    if positions is None and layout is None:
        raise _invalid_request()
    normalized_positions = _parse_positions(positions) if positions is not None else {}
    normalized_layout = _parse_layout(layout) if layout is not None else None
    if normalized_layout is not None:
        _validate_layout_fonts(
            normalized_layout, db, current_user.organization_id
        )
    image_data = _read_upload_data(file)
    try:
        image = validate_template_image(image_data, file.filename or "")
    except TemplateImageError:
        raise api_error(
            400,
            "TEMPLATE_INVALID_IMAGE",
            "The template image must be a valid PNG or JPEG no larger than 10 MB and 16383 pixels per side.",
        )

    if normalized_layout is None:
        try:
            normalized_layout = legacy_positions_to_layout(
                normalized_positions,
                {"width": image.width, "height": image.height},
            )
        except TemplateLayoutError as exc:
            raise _invalid_layout() from exc
        _validate_layout_fonts(
            normalized_layout, db, current_user.organization_id
        )
    _validate_layout_geometry(
        normalized_layout,
        {"width": image.width, "height": image.height},
    )

    stored_path = store_template_image(image)
    template = Template(
        organization_id=current_user.organization_id,
        name=name,
        background_image_path=reference(stored_path),
        positions=normalized_positions,
        dimensions={"width": image.width, "height": image.height},
        layout=normalized_layout,
        status=RecordStatus.active,
    )
    db.add(template)

    try:
        db.flush()
        record_audit(
            db,
            current_user,
            "template.create",
            "template",
            template.id,
            {"name": template.name},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            discard_file(stored_path)
        raise

    db.refresh(template)
    return template


@router.get("", response_model=list[TemplateResponse])
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    ensure_original_template(db, current_user.organization_id)
    db.commit()
    statement = owned_query(Template, current_user, db).where(Template.deleted_at.is_(None)).order_by(Template.builtin_key.is_(None), Template.id)
    return list(db.scalars(statement))


@router.post("/preview")
def preview_template(
    layout: Annotated[str, Form()],
    sample_values: Annotated[str, Form()],
    template_id: Annotated[int | None, Form(gt=0)] = None,
    file: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    if (template_id is None) == (file is None):
        raise _invalid_request()
    normalized_layout = _parse_layout(layout)
    _validate_layout_fonts(normalized_layout, db, current_user.organization_id)
    values = _parse_sample_values(sample_values)
    parsed_layout = TemplateLayout.model_validate(normalized_layout)
    for element in parsed_layout.elements:
        if element.source not in values and element.sample_value is not None:
            values[element.source] = element.sample_value
    values.setdefault("verification_code", "PREVIEW12345678")

    if template_id is not None:
        template = _owned_template(template_id, current_user, db)
        try:
            with Image.open(resolve_template_file(template.background_image_path)) as source:
                background = source.convert("RGB")
        except (OSError, ValueError) as exc:
            raise api_error(
                400,
                "TEMPLATE_INVALID_IMAGE",
                "The template background image is unavailable.",
            ) from exc
    else:
        try:
            image = validate_template_image(
                _read_upload_data(file), file.filename or ""
            )
        except TemplateImageError as exc:
            raise api_error(
                400,
                "TEMPLATE_INVALID_IMAGE",
                "The template image must be a valid PNG or JPEG no larger than 10 MB and 16383 pixels per side.",
            ) from exc
        with Image.open(BytesIO(image.data)) as source:
            background = source.convert("RGB")

    def preview_font_resolver(reference, weight):
        return resolve_font(
            db,
            current_user.organization_id,
            reference,
            weight=weight,
        )

    preview_signatures = {
        element_id: sample_signature()
        for element_id in signature_element_ids(parsed_layout)
    }
    try:
        rendered = render_layout(
            background,
            parsed_layout,
            values,
            preview_font_resolver,
            signatures=preview_signatures,
        )
        output = BytesIO()
        rendered.save(output, format="PNG")
    except QrTooSmallError as exc:
        raise api_error(
            400,
            "TEMPLATE_QR_TOO_SMALL",
            str(exc),
            element_id=exc.element_id,
        ) from exc
    except TextOverflowError as exc:
        raise api_error(
            400,
            "TEMPLATE_TEXT_OVERFLOW",
            str(exc),
            element_id=exc.element_id,
        ) from exc
    except SignatureTextOverflowError as exc:
        raise api_error(
            400,
            "TEMPLATE_TEXT_OVERFLOW",
            str(exc),
            element_id=exc.element_id,
        ) from exc
    except ThaiShapingUnavailableError as exc:
        raise api_error(
            503,
            "TEMPLATE_THAI_SHAPING_UNAVAILABLE",
            "Thai text rendering is not configured on this server.",
        ) from exc
    except Exception as exc:
        raise api_error(
            400,
            "TEMPLATE_PREVIEW_FAILED",
            "The template preview could not be rendered with these values.",
        ) from exc
    finally:
        background.close()
        if "rendered" in locals():
            rendered.close()
        close_prepared_signatures(preview_signatures)
    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{template_id}/background")
def get_template_background(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    template = _owned_template(template_id, current_user, db, include_deleted=True)
    try:
        path = resolve_template_file(template.background_image_path)
    except StoredFileUnavailableError as exc:
        raise _template_not_found() from exc
    media_type = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{template_id}", response_model=TemplateResponse)
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    return _owned_template(template_id, current_user, db, include_deleted=True)


@router.patch("/{template_id}", response_model=TemplateResponse)
def update_template(
    template_id: int,
    data: TemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    template = _owned_template(template_id, current_user, db)
    if template.is_builtin:
        raise api_error(409, "TEMPLATE_BUILTIN_PROTECTED", "ทำสำเนาแม่แบบต้นฉบับก่อนแก้ไข")
    changes = data.model_dump(exclude_unset=True)
    if not set(changes).issubset({"name", "positions", "layout"}):
        raise _invalid_update()
    if any(value is None for value in changes.values()):
        raise _invalid_request()
    if "positions" in changes:
        changes["positions"] = _validate_positions(changes["positions"])
        if "layout" not in changes:
            try:
                changes["layout"] = legacy_positions_to_layout(
                    changes["positions"], template.dimensions
                )
            except TemplateLayoutError as exc:
                raise _invalid_layout() from exc
    if "layout" in changes:
        changes["layout"] = _parse_layout(changes["layout"])
        _validate_immutable_sources(template.layout, changes["layout"])
        _validate_layout_geometry(changes["layout"], template.dimensions)
        _validate_layout_fonts(
            changes["layout"], db, current_user.organization_id
        )
    for field, value in changes.items():
        if value is not None:
            setattr(template, field, value)

    record_audit(
        db,
        current_user,
        "template.update",
        "template",
        template.id,
        {"fields": sorted(changes)},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(template)
    return template


@router.post("/{template_id}/deactivate", response_model=TemplateResponse)
def deactivate_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    template = _owned_template(template_id, current_user, db)
    if template.is_builtin:
        raise api_error(409, "TEMPLATE_BUILTIN_PROTECTED", "ไม่สามารถปิดใช้งานแม่แบบต้นฉบับของระบบได้")
    template.status = RecordStatus.inactive
    record_audit(
        db,
        current_user,
        "template.deactivate",
        "template",
        template.id,
        {"name": template.name},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    template = _owned_template(template_id, current_user, db, include_deleted=True)
    if template.is_builtin:
        raise api_error(409, "TEMPLATE_BUILTIN_PROTECTED", "ไม่สามารถลบแม่แบบต้นฉบับของระบบได้")
    if template.deleted_at is not None:
        return Response(status_code=204)
    # Keep the layout and background for certificates and batches already issued.
    template.deleted_at = datetime.now(timezone.utc)
    template.status = RecordStatus.inactive
    record_audit(db, current_user, "template.delete", "template", template.id, {"name": template.name})
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(status_code=204)


@router.post("/{template_id}/copy", response_model=TemplateResponse, status_code=201)
def copy_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    source = _owned_template(template_id, current_user, db)
    copied = Template(
        organization_id=current_user.organization_id,
        name=f"{source.name[:245]} (สำเนา)",
        background_image_path=source.background_image_path,
        positions=deepcopy(source.positions),
        dimensions=deepcopy(source.dimensions),
        layout=deepcopy(source.layout),
        status=RecordStatus.active,
    )
    db.add(copied)
    try:
        db.flush()
        record_audit(db, current_user, "template.copy", "template", copied.id, {"source_template_id": source.id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(copied)
    return copied
