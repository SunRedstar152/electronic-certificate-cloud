from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO
from typing import Any
import warnings

from PIL import Image, ImageDraw

from schemas.signatures import SignatureSubmission
from schemas.template_layout import TemplateLayout


PNG_DATA_URL_PREFIX = "data:image/png;base64,"
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_WIDTH = 4096
MAX_SIGNATURE_HEIGHT = 2048
NORMALIZED_MAX_SIZE = (2048, 1024)


class SignatureError(ValueError):
    code = "SIGNATURE_INVALID_PNG"
    public_message = "ข้อมูลลายเซ็นไม่ใช่ไฟล์ PNG ที่ถูกต้อง"


class SignatureRequiredError(SignatureError):
    code = "SIGNATURE_REQUIRED"
    public_message = "กรุณาใส่ลายเซ็น ชื่อ และตำแหน่งผู้ลงนาม"


class SignatureUnexpectedError(SignatureError):
    code = "SIGNATURE_UNEXPECTED"
    public_message = "ข้อมูลลายเซ็นไม่ตรงกับบล็อกลายเซ็นในแม่แบบ"


class SignatureInvalidPngError(SignatureError):
    code = "SIGNATURE_INVALID_PNG"


class SignatureTooLargeError(SignatureError):
    code = "SIGNATURE_TOO_LARGE"
    public_message = "ไฟล์ลายเซ็นมีขนาดใหญ่เกินกำหนด"


class SignatureTransparencyRequiredError(SignatureError):
    code = "SIGNATURE_TRANSPARENCY_REQUIRED"
    public_message = "ไฟล์ลายเซ็นต้องเป็น PNG ที่มีพื้นหลังโปร่งใส"


class SignatureEmptyError(SignatureError):
    code = "SIGNATURE_EMPTY"
    public_message = "กรุณาวาดหรือเลือกภาพลายเซ็นที่ไม่ว่าง"


class SignatureTextOverflowError(SignatureError):
    code = "SIGNATURE_TEXT_OVERFLOW"
    public_message = "ชื่อหรือตำแหน่งผู้ลงนามไม่พอดีกับกรอบลายเซ็น"

    def __init__(self, element_id: str):
        self.element_id = element_id
        super().__init__(self.public_message)


@dataclass
class PreparedSignature:
    image: Image.Image
    signer_name: str
    signer_position: str

    def close(self) -> None:
        self.image.close()


def layout_requires_signature(layout: object) -> bool:
    if isinstance(layout, TemplateLayout):
        parsed = layout
    elif isinstance(layout, dict) and layout.get("version") == 2:
        parsed = TemplateLayout.model_validate(layout)
    else:
        return False
    return any(item.visible and item.kind == "signature" for item in parsed.elements)


def _decode_png_data_url(data_url: str) -> bytes:
    if not data_url.startswith(PNG_DATA_URL_PREFIX):
        raise SignatureInvalidPngError()
    encoded = data_url[len(PNG_DATA_URL_PREFIX):]
    if len(encoded) > 4 * ((MAX_SIGNATURE_BYTES + 2) // 3):
        raise SignatureTooLargeError()
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureInvalidPngError() from exc
    if len(data) > MAX_SIGNATURE_BYTES:
        raise SignatureTooLargeError()
    return data


def prepare_signature(submission: SignatureSubmission) -> PreparedSignature:
    if not (
        submission.png_data_url
        and submission.signer_name
        and submission.signer_position
    ):
        raise SignatureRequiredError()
    data = _decode_png_data_url(submission.png_data_url)
    source = None
    converted = None
    alpha = None
    cropped = None
    sanitized = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(BytesIO(data))
            if source.format != "PNG":
                raise SignatureInvalidPngError()
            width, height = source.size
            if width > MAX_SIGNATURE_WIDTH or height > MAX_SIGNATURE_HEIGHT:
                raise SignatureTooLargeError()
            has_alpha = "A" in source.getbands() or "transparency" in source.info
            if not has_alpha:
                raise SignatureTransparencyRequiredError()
            source.load()
        converted = source.convert("RGBA")
        alpha = converted.getchannel("A")
        minimum_alpha, maximum_alpha = alpha.getextrema()
        if minimum_alpha == 255:
            raise SignatureTransparencyRequiredError()
        bounds = alpha.getbbox()
        if bounds is None or maximum_alpha == 0:
            raise SignatureEmptyError()
        cropped = converted.crop(bounds)
        cropped.thumbnail(NORMALIZED_MAX_SIZE, Image.Resampling.LANCZOS)
        sanitized = Image.new("RGBA", cropped.size, (0, 0, 0, 0))
        sanitized.alpha_composite(cropped)
        result_image = sanitized
        sanitized = None
        return PreparedSignature(
            image=result_image,
            signer_name=submission.signer_name,
            signer_position=submission.signer_position,
        )
    except SignatureError:
        raise
    except (Image.UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise SignatureInvalidPngError() from exc
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise SignatureTooLargeError() from exc
    finally:
        if sanitized is not None:
            sanitized.close()
        if cropped is not None:
            cropped.close()
        if alpha is not None:
            alpha.close()
        if converted is not None:
            converted.close()
        if source is not None:
            source.close()


def prepare_signature_for_layout(
    layout: object,
    submission: SignatureSubmission | None,
) -> PreparedSignature | None:
    required = layout_requires_signature(layout)
    if required and submission is None:
        raise SignatureRequiredError()
    if not required and submission is not None:
        raise SignatureUnexpectedError()
    return prepare_signature(submission) if submission is not None else None


def signature_element_ids(layout: object) -> list[str]:
    """Return the element IDs of all visible signature elements in the layout."""
    if isinstance(layout, TemplateLayout):
        parsed = layout
    elif isinstance(layout, dict) and layout.get("version") == 2:
        parsed = TemplateLayout.model_validate(layout)
    else:
        return []
    return [
        item.id for item in parsed.elements
        if item.visible and item.kind == "signature"
    ]


def prepare_signatures_for_layout(
    layout: object,
    submissions: dict[str, SignatureSubmission] | None,
    *,
    single_submission: SignatureSubmission | None = None,
) -> dict[str, PreparedSignature]:
    """Validate and prepare all signatures required by a layout.

    Supports both the new multi-signature dict and the legacy single
    submission for backward compatibility.
    """
    element_ids = signature_element_ids(layout)
    if submissions is not None and single_submission is not None:
        raise SignatureUnexpectedError()
    if not element_ids:
        if submissions or single_submission:
            raise SignatureUnexpectedError()
        return {}

    # Backward compatibility: single submission → map to the first element
    if submissions is None and single_submission is not None:
        submissions = {element_ids[0]: single_submission}
    elif submissions is None:
        raise SignatureRequiredError()

    if set(submissions) - set(element_ids):
        raise SignatureUnexpectedError()
    if set(element_ids) - set(submissions):
        raise SignatureRequiredError()

    prepared: dict[str, PreparedSignature] = {}
    try:
        for element_id in element_ids:
            sub = submissions.get(element_id)
            if sub is None:
                raise SignatureRequiredError()
            prepared[element_id] = prepare_signature(sub)
        return prepared
    except Exception:
        for sig in prepared.values():
            sig.close()
        raise


def close_prepared_signatures(signatures: dict[str, PreparedSignature]) -> None:
    """Close all PIL images in a prepared signatures dict."""
    for sig in signatures.values():
        sig.close()


def sample_signature() -> PreparedSignature:
    image = Image.new("RGBA", (360, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.line(
        [(30, 85), (70, 35), (95, 92), (125, 28), (150, 83), (210, 58), (325, 40)],
        fill=(30, 30, 30, 255),
        width=6,
        joint="curve",
    )
    return PreparedSignature(image=image, signer_name="ชื่อผู้ลงนาม", signer_position="ตำแหน่ง")


__all__ = [
    "MAX_SIGNATURE_BYTES",
    "MAX_SIGNATURE_HEIGHT",
    "MAX_SIGNATURE_WIDTH",
    "NORMALIZED_MAX_SIZE",
    "PNG_DATA_URL_PREFIX",
    "PreparedSignature",
    "SignatureEmptyError",
    "SignatureError",
    "SignatureInvalidPngError",
    "SignatureRequiredError",
    "SignatureTextOverflowError",
    "SignatureTooLargeError",
    "SignatureTransparencyRequiredError",
    "SignatureUnexpectedError",
    "close_prepared_signatures",
    "layout_requires_signature",
    "prepare_signature",
    "prepare_signature_for_layout",
    "prepare_signatures_for_layout",
    "sample_signature",
    "signature_element_ids",
]
