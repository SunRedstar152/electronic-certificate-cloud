from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from services.object_storage import reference, discard_file

from auth import require_organization_user
from core.errors import api_error
from database import get_db
from models import FontAsset, RecordStatus, Template, User
from schemas.fonts import FontResponse
from services.audit import record_audit
from services.font_files import (
    MAX_FONT_BYTES,
    FontFileError,
    FontThaiUnsupportedError,
    store_font,
    validate_font_upload,
)
from services.font_registry import BUILTIN_FONTS


router = APIRouter(prefix="/fonts", tags=["fonts"])


def _font_not_found():
    return api_error(404, "FONT_NOT_FOUND", "The font could not be found.")


def _read_upload_data(file: UploadFile) -> bytes:
    return file.file.read(MAX_FONT_BYTES + 1)


def _uploaded_response(asset: FontAsset) -> dict[str, Any]:
    return {
        "source": "uploaded",
        "key": None,
        "id": asset.id,
        "organization_id": asset.organization_id,
        "display_name": asset.display_name,
        "supports_thai": asset.supports_thai,
        "status": asset.status,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def _layout_uses_font(layout: object, font_id: int) -> bool:
    if not isinstance(layout, dict):
        return False
    elements = layout.get("elements")
    if not isinstance(elements, list):
        return False
    for element in elements:
        if not isinstance(element, dict):
            continue
        style = element.get("style")
        if not isinstance(style, dict):
            continue
        reference = style.get("font")
        if (
            isinstance(reference, dict)
            and reference.get("source") == "uploaded"
            and reference.get("id") == font_id
        ):
            return True
    return False


@router.get("", response_model=list[FontResponse])
def list_fonts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    builtins = [
        {
            "source": "builtin",
            "key": font.key,
            "id": None,
            "organization_id": None,
            "display_name": font.display_name,
            "supports_thai": True,
            "status": RecordStatus.active,
            "created_at": None,
            "updated_at": None,
        }
        for font in BUILTIN_FONTS.values()
    ]
    uploads = list(
        db.scalars(
            select(FontAsset)
            .where(
                FontAsset.organization_id == current_user.organization_id,
                FontAsset.status == RecordStatus.active,
            )
            .order_by(FontAsset.id)
        )
    )
    return builtins + [_uploaded_response(asset) for asset in uploads]


@router.post("", response_model=FontResponse, status_code=status.HTTP_201_CREATED)
def upload_font(
    response: Response,
    display_name: Annotated[str, Form(min_length=1, max_length=255)],
    file: Annotated[UploadFile, File()],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        validated = validate_font_upload(
            _read_upload_data(file), file.filename or ""
        )
    except FontThaiUnsupportedError as exc:
        raise api_error(
            400,
            "FONT_THAI_UNSUPPORTED",
            "The font must contain Thai letters, marks and digits.",
        ) from exc
    except FontFileError as exc:
        raise api_error(
            400,
            "FONT_INVALID_FILE",
            "The font must be a valid Thai TTF or OTF file no larger than 5 MB.",
        ) from exc

    duplicates = db.scalars(
        select(FontAsset).where(
            FontAsset.organization_id == current_user.organization_id,
            FontAsset.display_name == display_name,
            FontAsset.status == RecordStatus.active
        )
    ).all()

    if duplicates:
        for dup in duplicates:
            dup.status = RecordStatus.inactive
        db.commit()

    active_fonts_count = db.scalar(
        select(func.count(FontAsset.id)).where(
            FontAsset.organization_id == current_user.organization_id,
            FontAsset.status == RecordStatus.active
        )
    )

    existing = db.scalar(
        select(FontAsset).where(
            FontAsset.organization_id == current_user.organization_id,
            FontAsset.sha256 == validated.sha256,
        )
    )

    if existing is None and active_fonts_count >= 3:
        raise api_error(
            400,
            "FONT_LIMIT_EXCEEDED",
            "อัปโหลดได้สูงสุด 3 ฟอนต์ หากต้องการอัปโหลดใหม่ กรุณาลบฟอนต์เดิมออกก่อน"
        )
    elif existing is not None and existing.status != RecordStatus.active and active_fonts_count >= 3:
        raise api_error(
            400,
            "FONT_LIMIT_EXCEEDED",
            "อัปโหลดได้สูงสุด 3 ฟอนต์ หากต้องการอัปโหลดใหม่ กรุณาลบฟอนต์เดิมออกก่อน"
        )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        if existing.status != RecordStatus.active:
            existing.status = RecordStatus.active
            existing.display_name = display_name
            record_audit(
                db,
                current_user,
                "font.reactivate",
                "font",
                existing.id,
                {"display_name": existing.display_name},
            )
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            db.refresh(existing)
        return _uploaded_response(existing)

    stored_path = store_font(validated, current_user.organization_id)
    asset = FontAsset(
        organization_id=current_user.organization_id,
        display_name=display_name,
        original_filename=file.filename or "font",
        file_path=reference(stored_path),
        sha256=validated.sha256,
        format=validated.format,
        supports_thai=validated.supports_thai,
        status=RecordStatus.active,
    )
    db.add(asset)
    try:
        db.flush()
        record_audit(
            db,
            current_user,
            "font.create",
            "font",
            asset.id,
            {"display_name": asset.display_name, "format": asset.format},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            discard_file(stored_path)
        raise
    db.refresh(asset)
    return _uploaded_response(asset)


@router.delete("/{font_id}", response_model=FontResponse)
def deactivate_font(
    font_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    asset = db.scalar(
        select(FontAsset).where(
            FontAsset.id == font_id,
            FontAsset.organization_id == current_user.organization_id,
        )
    )
    if asset is None:
        raise _font_not_found()

    templates = db.scalars(
        select(Template).where(
            Template.organization_id == current_user.organization_id,
            Template.status == RecordStatus.active,
        )
    )
    if any(_layout_uses_font(template.layout, asset.id) for template in templates):
        raise api_error(
            409,
            "FONT_IN_USE",
            "The font is used by an active template and cannot be deactivated.",
        )

    asset.status = RecordStatus.inactive
    record_audit(
        db,
        current_user,
        "font.deactivate",
        "font",
        asset.id,
        {"display_name": asset.display_name},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(asset)
    return _uploaded_response(asset)
