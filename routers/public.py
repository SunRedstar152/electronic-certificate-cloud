from io import BytesIO
import os
from pathlib import Path
import stat
from typing import BinaryIO, Iterator, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from core.errors import api_error
from database import get_db
from models import Activity, Certificate, CertificateStatus, CertificateType, Organization
from services.certificate_images import is_webp, png_export
from config import settings
from services.storage_paths import StoredFileUnavailableError, resolve_certificate_file


router = APIRouter(prefix="/api/public/certificates", tags=["Public certificates"])
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


def _not_found():
    return api_error(404, "CERTIFICATE_NOT_FOUND", "Certificate not found.")


def _revoked():
    return api_error(
        410,
        "CERTIFICATE_REVOKED",
        "This certificate has been revoked.",
    )


def _public_certificate(
    db: Session, code: str
) -> tuple | None:
    return db.execute(
        select(
            Certificate,
            Organization.name,
            Activity.activity_name,
            CertificateType.name,
        )
        .join(Organization, Certificate.organization_id == Organization.id)
        .outerjoin(Activity, Certificate.activity_id == Activity.id)
        .outerjoin(CertificateType, Certificate.certificate_type_id == CertificateType.id)
        .where(Certificate.verification_code == code.strip().upper())
    ).one_or_none()


def _public_summary(certificate: Certificate, organization_name: str, activity_name: str | None = None, certificate_type_name: str | None = None) -> dict:
    summary = {
        "status": certificate.status.value,
        "verification_code": certificate.verification_code,
        "recipient_name": certificate.recipient_name,
        "topic": certificate.topic,
        "issue_date": certificate.issue_date.isoformat(),
        "organization": {"name": organization_name},
    }
    if activity_name:
        summary["activity_name"] = activity_name
    if certificate_type_name:
        summary["certificate_type"] = certificate_type_name
    if hasattr(certificate, "recipient_type") and certificate.recipient_type:
        summary["recipient_type"] = certificate.recipient_type.value
    if hasattr(certificate, "external_organization") and certificate.external_organization:
        summary["external_organization"] = certificate.external_organization
    return summary


def _stored_certificate_path(image_path: str | None) -> Path | None:
    if not image_path:
        return None
    try:
        return resolve_certificate_file(image_path)
    except StoredFileUnavailableError:
        return None


def _open_certificate_file(image_path: str | None) -> BinaryIO | None:
    candidate = _stored_certificate_path(image_path)
    if candidate is None:
        return None
    try:
        before_open = candidate.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(before_open.st_mode):
        return None

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        return None

    opened_file = os.fdopen(descriptor, "rb")
    try:
        opened_stat = os.fstat(opened_file.fileno())
        after_open = candidate.lstat()
    except OSError:
        opened_file.close()
        return None
    if (
        not stat.S_ISREG(opened_stat.st_mode)
        or not os.path.samestat(before_open, opened_stat)
        or not os.path.samestat(opened_stat, after_open)
    ):
        opened_file.close()
        return None
    return opened_file


def _file_chunks(file: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := file.read(_DOWNLOAD_CHUNK_SIZE):
            yield chunk
    finally:
        file.close()


def _active_certificate_image(db: Session, code: str) -> tuple[Certificate, BinaryIO]:
    record = _public_certificate(db, code)
    if record is None:
        raise _not_found()
    certificate, _organization_name, _activity_name, _certificate_type_name = record
    if certificate.status == CertificateStatus.revoked:
        raise _revoked()

    image_file = _open_certificate_file(certificate.image_path)
    if image_file is None:
        raise _not_found()
    return certificate, image_file


def _convert_certificate_image(image_file: BinaryIO, file_format: str) -> BytesIO:
    output = BytesIO()
    try:
        with Image.open(image_file) as source:
            source.load()
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                converted = Image.new("RGB", rgba.size, "white")
                converted.paste(rgba, mask=rgba.getchannel("A"))
            else:
                converted = source.convert("RGB")
            if file_format == "jpg":
                converted.save(output, format="JPEG", quality=95, optimize=True)
            else:
                converted.save(output, format="PDF", resolution=150.0)
    except (UnidentifiedImageError, OSError, ValueError):
        output.close()
        raise _not_found()
    finally:
        image_file.close()
    output.seek(0)
    return output


@router.get("/{code}/image")
def view_certificate(code: str, db: Session = Depends(get_db)):
    _certificate, image_file = _active_certificate_image(db, code)
    media_type = "image/webp" if is_webp(image_file) else "image/png"
    return StreamingResponse(
        _file_chunks(image_file),
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(image_file.close),
    )


@router.get("/{code}/download")
def download_certificate(
    code: str,
    file_format: Literal["png", "jpg", "pdf"] = Query("png", alias="format"),
    db: Session = Depends(get_db),
):
    certificate, image_file = _active_certificate_image(db, code)
    if file_format == "png":
        if is_webp(image_file):
            original = image_file
            try:
                image_file = png_export(original, compress_level=settings.PNG_COMPRESS_LEVEL)
            except (OSError, ValueError):
                raise _not_found()
            finally:
                original.close()
        return StreamingResponse(
            _file_chunks(image_file),
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    f'attachment; filename="certificate-{certificate.verification_code}.png"'
                )
            },
            background=BackgroundTask(image_file.close),
        )

    output = _convert_certificate_image(image_file, file_format)
    media_type = "image/jpeg" if file_format == "jpg" else "application/pdf"
    return StreamingResponse(
        _file_chunks(output),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'attachment; filename="certificate-{certificate.verification_code}.{file_format}"'
            )
        },
        background=BackgroundTask(output.close),
    )


@router.get("/{code}")
def verify_certificate(code: str, db: Session = Depends(get_db)):
    record = _public_certificate(db, code)
    if record is None:
        raise _not_found()
    certificate, organization_name, activity_name, certificate_type_name = record
    return _public_summary(certificate, organization_name, activity_name, certificate_type_name)
