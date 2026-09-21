from __future__ import annotations

import csv
import shutil
from config import settings
from services.certificate_images import is_webp, png_export
from dataclasses import dataclass
from io import StringIO
import re
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterator
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from models import Batch, BatchRow, CertificateStatus, User
from services.batch_service import BatchNotFoundError, get_owned_batch
from services.storage_paths import StoredFileUnavailableError, resolve_certificate_file


_ARCHIVE_MEMORY_LIMIT = 8 * 1024 * 1024
_STREAM_CHUNK_SIZE = 64 * 1024
_UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^\w.-]+", re.UNICODE)
_REPEATED_UNDERSCORES = re.compile(r"_+")


@dataclass
class ArchiveDownload:
    file: BinaryIO
    filename: str

    def close(self) -> None:
        self.file.close()


def _safe_component(value: str, fallback: str) -> str:
    normalized = _UNSAFE_FILENAME_CHARACTERS.sub("_", value.strip())
    normalized = _REPEATED_UNDERSCORES.sub("_", normalized).strip("._")
    if not normalized or normalized in {".", ".."}:
        normalized = fallback
    return normalized[:180]


def _archive_name(reference: str, verification_code: str) -> str:
    safe_reference = _safe_component(reference, "recipient")
    safe_code = _safe_component(verification_code, "certificate")
    return f"{safe_reference}-{safe_code}.png"


def _manifest_bytes(omissions: list[dict[str, str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["recipient_reference", "verification_code", "reason"],
    )
    writer.writeheader()
    writer.writerows(omissions)
    return output.getvalue().encode("utf-8-sig")


def _omission(row: BatchRow, reason: str) -> dict[str, str]:
    certificate = row.certificate
    return {
        "recipient_reference": row.recipient_reference or "",
        "verification_code": certificate.verification_code if certificate else "",
        "reason": reason,
    }


def build_batch_archive(
    db: Session,
    actor: User,
    batch_id: int,
) -> ArchiveDownload:
    get_owned_batch(db, actor, batch_id)
    batch = db.scalar(
        select(Batch)
        .where(Batch.id == batch_id, Batch.organization_id == actor.organization_id)
        .options(selectinload(Batch.rows).selectinload(BatchRow.certificate))
        .execution_options(populate_existing=True)
    )
    if batch is None:
        raise BatchNotFoundError("Batch not found.")

    stream = SpooledTemporaryFile(max_size=_ARCHIVE_MEMORY_LIMIT, mode="w+b")
    omissions: list[dict[str, str]] = []
    try:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
            for row in sorted(batch.rows, key=lambda item: (item.row_number, item.id)):
                certificate = row.certificate
                if certificate is None:
                    omissions.append(
                        _omission(row, row.error_code or "CERTIFICATE_NOT_ISSUED")
                    )
                    continue
                if certificate.status != CertificateStatus.active:
                    omissions.append(_omission(row, "CERTIFICATE_REVOKED"))
                    continue
                if not certificate.image_path:
                    omissions.append(_omission(row, "CERTIFICATE_IMAGE_MISSING"))
                    continue
                try:
                    image_path = resolve_certificate_file(certificate.image_path)
                except StoredFileUnavailableError as exc:
                    reason = (
                        "CERTIFICATE_IMAGE_PATH_UNSAFE"
                        if exc.reason == "unsafe"
                        else "CERTIFICATE_IMAGE_MISSING"
                    )
                    omissions.append(_omission(row, reason))
                    continue
                name = _archive_name(certificate.recipient_reference, certificate.verification_code)
                try:
                    with image_path.open("rb") as source:
                        png = png_export(source, compress_level=settings.PNG_COMPRESS_LEVEL) if is_webp(source) else None
                except (OSError, ValueError):
                    omissions.append(_omission(row, "CERTIFICATE_IMAGE_UNREADABLE"))
                    continue
                # Archive I/O errors must fail the download, not publish a partial ZIP.
                if png is None:
                    archive.write(image_path, arcname=name)
                else:
                    with png, archive.open(name, "w", force_zip64=True) as member:
                        shutil.copyfileobj(png, member, length=_STREAM_CHUNK_SIZE)
            archive.writestr("manifest.csv", _manifest_bytes(omissions))
        stream.seek(0)
        return ArchiveDownload(
            file=stream,
            filename=f"batch-{batch_id}-certificates.zip",
        )
    except Exception:
        stream.close()
        raise


def iter_archive(download: ArchiveDownload) -> Iterator[bytes]:
    try:
        while chunk := download.file.read(_STREAM_CHUNK_SIZE):
            yield chunk
    finally:
        download.close()


__all__ = ["ArchiveDownload", "build_batch_archive", "iter_archive"]
