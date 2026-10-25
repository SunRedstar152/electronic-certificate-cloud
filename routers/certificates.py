from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import FileResponse, StreamingResponse, Response
from starlette.background import BackgroundTask
from services.certificate_images import is_webp, png_export
from config import settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import require_organization_user
from core.errors import api_error
from database import get_db
from models import Certificate, User
from schemas import (
    CertificateCreate,
    CertificateIssuanceRequest,
    CertificateResponse,
    CertificateRevokeRequest,
)
from services.certificates import (
    CertificateNotRevokedError,
    delete_certificate as delete_owned_certificate,
    CertificateFieldInvalidError,
    CertificateFontUnavailableError,
    CertificateNotFoundError,
    CertificateQrInvalidError,
    CertificateRequiredFieldMissingError,
    CertificateTemplateNotFoundError,
    CertificateActivityNotApprovedError,
    CertificateThaiShapingUnavailableError,
    CertificateTextOverflowError,
    IdempotencyKeyError,
    RevocationReasonError,
    create_certificate as issue_certificate,
    get_owned_certificate,
    revoke_certificate as revoke_owned_certificate,
)
from services.storage_paths import StoredFileUnavailableError, resolve_certificate_file
from services.signatures import SignatureError


router = APIRouter(prefix="/certificates", tags=["Certificates"])


@router.delete("/{code}", status_code=204)
def delete_certificate(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    from services.object_storage import StorageUnavailableError
    try:
        delete_owned_certificate(db, current_user, code)
    except CertificateNotFoundError:
        raise _not_found()
    except CertificateNotRevokedError:
        raise api_error(409, "CERTIFICATE_NOT_REVOKED", "กรุณาเพิกถอนเกียรติบัตรก่อนลบ")
    except (StorageUnavailableError, OSError, ValueError):
        raise api_error(503, "CERTIFICATE_DELETE_FAILED", "ลบไฟล์ไม่สำเร็จ กรุณาลองอีกครั้ง")
    return Response(status_code=204)


_FIELD_ERROR_MESSAGES = {
    "range_incomplete": "กรุณากรอกค่าเริ่มและค่าสิ้นสุดให้ครบทั้งสองช่อง",
    "range_reversed": "ค่าสิ้นสุดต้องไม่อยู่ก่อนค่าเริ่ม กรุณาตรวจวันที่ ปี หรือเวลาในทั้งสองช่อง",
    "date_invalid": "วันที่ไม่ถูกต้อง กรุณาเลือกวันที่ใหม่จากปฏิทิน",
    "time_invalid": "เวลาไม่ถูกต้อง กรุณาเลือกเวลาใหม่",
    "text_invalid": "กรุณากรอกข้อความไม่เกิน 2,000 ตัวอักษร",
    "unknown_field": "ช่องข้อมูลไม่ตรงกับแม่แบบปัจจุบัน กรุณาเลือกแม่แบบใหม่แล้วกรอกข้อมูลอีกครั้ง",
    "layout_invalid": "แม่แบบมีรูปแบบข้อมูลไม่ถูกต้อง กรุณาติดต่อผู้ดูแลหน่วยงาน",
    "payload_too_large": "ข้อมูลเพิ่มเติมมีขนาดใหญ่เกินกำหนด กรุณาลดข้อความ",
}


def _not_found():
    return api_error(404, "CERTIFICATE_NOT_FOUND", "Certificate not found.")


def _template_not_found():
    return api_error(
        404,
        "CERTIFICATE_TEMPLATE_NOT_FOUND",
        "An active certificate template was not found.",
    )


@router.post("", response_model=CertificateResponse, status_code=status.HTTP_201_CREATED)
def create_certificate(
    payload: CertificateIssuanceRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=255)
    ] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        certificate_payload = CertificateCreate.model_validate(
            payload.model_dump(exclude={"signature", "signatures"})
        )
        return issue_certificate(
            db,
            current_user,
            certificate_payload,
            idempotency_key,
            signature=payload.signature,
            signatures=payload.signatures,
        )
    except CertificateTemplateNotFoundError:
        raise _template_not_found()
    except CertificateActivityNotApprovedError:
        raise api_error(422, "CERTIFICATE_ACTIVITY_NOT_APPROVED", "Only approved activities may issue certificates.")
    except IdempotencyKeyError:
        raise api_error(
            422,
            "REQUEST_VALIDATION_ERROR",
            "The request contains invalid or missing fields.",
        )
    except SignatureError as exc:
        raise api_error(422, exc.code, exc.public_message)
    except CertificateRequiredFieldMissingError:
        raise api_error(
            422,
            "CERTIFICATE_REQUIRED_FIELD_MISSING",
            "A required template field is missing.",
        )
    except CertificateFieldInvalidError as exc:
        message = _FIELD_ERROR_MESSAGES.get(exc.reason, "ข้อมูลในแม่แบบไม่ถูกต้อง กรุณาตรวจช่องข้อมูลเพิ่มเติม")
        if exc.field_label:
            message = f'ช่อง “{exc.field_label}”: {message}'
        raise api_error(
            422,
            "CERTIFICATE_FIELD_INVALID",
            message,
            field_key=exc.field_key,
            field_label=exc.field_label,
            reason=exc.reason,
        )
    except CertificateTextOverflowError:
        raise api_error(
            422,
            "CERTIFICATE_TEXT_OVERFLOW",
            "A field value does not fit its template area.",
        )
    except CertificateFontUnavailableError:
        raise api_error(
            422,
            "CERTIFICATE_FONT_UNAVAILABLE",
            "A font required by the template is unavailable.",
        )
    except CertificateQrInvalidError:
        raise api_error(
            422,
            "CERTIFICATE_QR_INVALID",
            "A QR field in the template is too small to render safely.",
        )
    except CertificateThaiShapingUnavailableError:
        raise api_error(
            503,
            "CERTIFICATE_THAI_SHAPING_UNAVAILABLE",
            "Thai text rendering is not configured on this server.",
        )


@router.get("", response_model=list[CertificateResponse])
def list_certificates(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    statement = (
        select(Certificate)
        .where(Certificate.organization_id == current_user.organization_id)
        .order_by(Certificate.id)
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement))


@router.get("/{code}/download")
def download_certificate(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        certificate = get_owned_certificate(db, current_user, code)
    except CertificateNotFoundError:
        raise _not_found()

    try:
        image_path = resolve_certificate_file(certificate.image_path or "")
    except StoredFileUnavailableError:
        raise _not_found()
    try:
        with image_path.open("rb") as source:
            if is_webp(source):
                output = png_export(source, compress_level=settings.PNG_COMPRESS_LEVEL)
                def chunks():
                    try:
                        while data := output.read(64 * 1024):
                            yield data
                    finally:
                        output.close()
                return StreamingResponse(chunks(), media_type="image/png",
                    headers={"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff",
                             "Content-Disposition":f'attachment; filename="certificate-{certificate.verification_code}.png"'},
                    background=BackgroundTask(output.close))
    except (OSError, ValueError):
        raise _not_found()
    return FileResponse(
        image_path,
        media_type="image/png",
        filename=f"certificate-{certificate.verification_code}.png",
    )


@router.post("/{code}/revoke", response_model=CertificateResponse)
def revoke_certificate(
    code: str,
    payload: CertificateRevokeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organization_user),
):
    try:
        return revoke_owned_certificate(db, current_user, code, payload.reason)
    except CertificateNotFoundError:
        raise _not_found()
    except RevocationReasonError:
        raise api_error(
            422,
            "REQUEST_VALIDATION_ERROR",
            "The request contains invalid or missing fields.",
        )
