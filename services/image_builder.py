from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw, ImageFont
import qrcode
from sqlalchemy.orm import object_session

from services.object_storage import StorageUnavailableError
from config import settings
from models import Certificate, Template
from schemas.template_layout import FontReference, TemplateLayout, TextStyle
from services.font_registry import FontNotFoundError, resolve_font
from services.template_layout import (
    TemplateLayoutError,
    legacy_positions_to_layout,
    normalize_layout,
)
from services.storage_paths import StoredFileUnavailableError, resolve_template_file
from services.text_layout import (
    TextOverflowError,
    ThaiShapingUnavailableError,
    layout_engine_for_text,
    layout_text,
)
from services.value_formatter import ValueFormatError, format_element_value
from services.signatures import PreparedSignature, SignatureTextOverflowError
from services.certificate_images import save_image, WEBP_MAX_DIMENSION
from services.certificate_qr import issuance_metadata, issuance_png_info, record_qr_region, verification_url


class ImageRenderError(RuntimeError):
    def __init__(self, message: str = "The certificate image could not be rendered."):
        super().__init__(message)


class QrTooSmallError(ValueError):
    def __init__(self, element_id: str):
        self.element_id = element_id
        super().__init__(
            f'QR element "{element_id}" must have positive width and height.'
        )


FontResolver = Callable[[FontReference, int], str | Path]
MIN_QR_SIDE_PX = 1


def _verification_url(code: str) -> str:
    return verification_url(str(settings.PUBLIC_BASE_URL), code)


def _pixel_box(
    normalized_box: Any,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int]:
    left = round(normalized_box.x * canvas_width)
    top = round(normalized_box.y * canvas_height)
    right = round((normalized_box.x + normalized_box.width) * canvas_width)
    bottom = round((normalized_box.y + normalized_box.height) * canvas_height)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _draw_text_in_box(
    canvas: Image.Image,
    text: str,
    pixel_box: tuple[int, int, int, int],
    style: TextStyle,
    element_id: str,
    font_resolver: FontResolver,
    *,
    wrap: bool = False,
) -> float:
    left, top, right, bottom = pixel_box
    font_path = Path(font_resolver(style.font, style.weight))
    try:
        font = ImageFont.truetype(
            font_path.as_posix(),
            style.font_size_px,
            layout_engine=layout_engine_for_text(text),
        )
    except OSError as exc:
        raise ImageRenderError("A certificate rendering font is unavailable.") from exc
    # The block controls alignment and wrapping; only the page clips the text.
    draw = ImageDraw.Draw(canvas)
    lines = layout_text(
        draw,
        text,
        font,
        (left, top, max(left + 1, right), max(top + 1, bottom)),
        style,
        element_id=element_id,
        allow_overflow=True,
        wrap=wrap,
    )
    options = (
        {"language": "th"}
        if font.layout_engine == ImageFont.Layout.RAQM
        else {}
    )
    text_bottom = float(top)
    for line in lines:
        draw.text(
            line.position,
            line.text,
            font=font,
            fill=style.color,
            anchor=line.anchor,
            **options,
        )
        bounds = draw.textbbox(line.position, line.text, font=font, anchor=line.anchor, **options)
        text_bottom = max(text_bottom, bounds[3])
    return text_bottom


def _parenthesized_name(value: str) -> str:
    stripped = value.strip()
    return (
        stripped
        if stripped.startswith("(") and stripped.endswith(")")
        else f"({stripped})"
    )


def _signature_regions(
    left: int, top: int, right: int, bottom: int
) -> tuple[tuple[int, int, int, int], ...]:
    height = bottom - top
    image_bottom = top + round(height * 0.60)
    name_bottom = top + round(height * 0.80)
    return (
        (left, top, right, image_bottom),
        (left, image_bottom, right, name_bottom),
        (left, name_bottom, right, bottom),
    )


def _contain_and_alpha_paste(
    canvas: Image.Image,
    source: Image.Image,
    pixel_box: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = pixel_box
    contained = source.copy()
    try:
        contained.thumbnail(
            (max(1, right - left), max(1, bottom - top)),
            Image.Resampling.LANCZOS,
        )
        paste_left = left + ((right - left) - contained.width) // 2
        paste_top = top + ((bottom - top) - contained.height) // 2
        canvas.paste(contained, (paste_left, paste_top), contained)
    finally:
        contained.close()


def _qr_caption_height(box_width: int, box_height: int) -> int:
    return min(max(0, box_height - 1), max(1, round(min(box_width, box_height) * 0.16)))


def qr_code_side(box_width: int, box_height: int, *, include_label: bool = True) -> int:
    footer = _qr_caption_height(box_width, box_height) if include_label else 0
    return max(1, min(box_width, box_height - footer))


def _draw_qr_in_box(
    canvas: Image.Image,
    url: str,
    pixel_box: tuple[int, int, int, int],
    element_id: str,
    font_resolver: FontResolver,
    *, show_id: bool = True,
) -> tuple[int, int, int, int]:
    try:
        code = parse_qs(urlsplit(url).query).get("code", [""])[0].strip()
    except ValueError:
        code = ""
    label = f"ID: {code}" if code and show_id else ""
    left, top, right, bottom = pixel_box
    width, height = right - left, bottom - top
    side = qr_code_side(width, height, include_label=bool(label))
    if side < MIN_QR_SIDE_PX:
        raise QrTooSmallError(element_id)
    footer = _qr_caption_height(width, height) if label else 0
    qr_left = left + (width - side) // 2
    qr_top = top + (height - side - footer) // 2

    # Keep the QR's white quiet zone and a separate, high-contrast caption.
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (qr_left, qr_top, qr_left + side - 1, qr_top + side + footer - 1),
        fill="white",
    )
    with qrcode.make(url).convert("RGB") as source:
        with source.resize((side, side), Image.Resampling.NEAREST) as qr_image:
            canvas.paste(qr_image, (qr_left, qr_top))
    if not label or not footer:
        return (qr_left, qr_top, qr_left + side, qr_top + side)

    # Draw small captions at a readable working resolution, then fit the whole
    # line into a separate layer so even a tiny block cannot clip onto its peers.
    working_width = max(256, side)
    working_height = max(32, round(footer * working_width / side))
    font_path = font_resolver(FontReference(source="builtin", key="sarabun"), 400)
    font_size = max(8, round(working_width * 0.085))
    while True:
        font = ImageFont.truetype(
            str(font_path), font_size, layout_engine=layout_engine_for_text(label)
        )
        bounds = font.getbbox(label, anchor="mt")
        text_height = bounds[3] - bounds[1]
        if font_size <= 1 or (bounds[2] - bounds[0] <= working_width - 8 and text_height <= working_height - 4):
            break
        font_size -= 1
    with Image.new("RGB", (working_width, working_height), "white") as caption:
        ImageDraw.Draw(caption).text(
            (working_width / 2, (working_height - text_height) / 2),
            label, font=font, fill="black", anchor="mt",
        )
        with caption.resize((side, footer), Image.Resampling.LANCZOS) as fitted:
            canvas.paste(fitted, (qr_left, qr_top + side))
    return (qr_left, qr_top, qr_left + side, qr_top + side)


def _draw_id_text_in_box(canvas, text, pixel_box, style, font_resolver):
    """Fit the editable ID as transparent text, including in very small boxes."""
    left, top, right, bottom = pixel_box
    width, height = right - left, bottom - top
    scale = min(4, max(1, 256 / width, 32 / height))
    work_width, work_height = round(width * scale), round(height * scale)
    font_path = font_resolver(style.font, style.weight)
    size = max(1, round(style.font_size_px * scale))
    while True:
        font = ImageFont.truetype(str(font_path), size, layout_engine=layout_engine_for_text(text))
        bounds = font.getbbox(text)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if size <= 1 or (text_width <= work_width and text_height <= work_height):
            break
        size = max(1, min(size - 1, int(size * min(work_width / max(1, text_width), work_height / max(1, text_height)))))
    x = 0 if style.align == "left" else work_width - text_width if style.align == "right" else (work_width - text_width) / 2
    with Image.new("RGBA", (work_width, work_height), (0, 0, 0, 0)) as layer:
        ImageDraw.Draw(layer).text((x - bounds[0], (work_height - text_height) / 2 - bounds[1]), text, font=font, fill=style.color)
        with layer.resize((width, height), Image.Resampling.LANCZOS) as fitted:
            canvas.paste(fitted, (left, top), fitted)


def render_layout(
    background: Image.Image,
    layout: TemplateLayout | dict[str, Any],
    values: dict[str, Any],
    font_resolver: FontResolver,
    signatures: dict[str, PreparedSignature] | None = None,
    *,
    signature: PreparedSignature | None = None,
) -> Image.Image:
    parsed = (
        layout
        if isinstance(layout, TemplateLayout)
        else TemplateLayout.model_validate(layout)
    )
    canvas = background.copy() if background.mode == "RGB" else background.convert("RGB")
    canvas.info["_pkru_qr_regions"] = []
    canvas_width, canvas_height = canvas.size

    for element in parsed.elements:
        if not element.visible:
            continue
        left, top, right, bottom = _pixel_box(
            element.box, canvas_width, canvas_height
        )
        if element.kind == "signature":
            element_signature = (signatures or {}).get(element.id) if signatures is not None else signature
            if element_signature is None:
                continue
            image_box, name_box, position_box = _signature_regions(
                left, top, right, bottom
            )
            _contain_and_alpha_paste(canvas, element_signature.image, image_box)
            try:
                name_bottom = _draw_text_in_box(
                    canvas,
                    _parenthesized_name(element_signature.signer_name),
                    name_box,
                    element.style,
                    element.id,
                    font_resolver,
                )
                # A short block must not place the position on top of the name.
                gap = max(1, round(element.style.font_size_px * 0.15))
                offset = max(0, round(name_bottom + gap - position_box[1]))
                position_box = (
                    position_box[0], position_box[1] + offset,
                    position_box[2], position_box[3] + offset,
                )
                position_style = element.style.model_copy(
                    update={
                        "font_size_px": max(
                            8, round(element.style.font_size_px * 0.85)
                        )
                    }
                )
                _draw_text_in_box(
                    canvas,
                    element_signature.signer_position,
                    position_box,
                    position_style,
                    element.id,
                    font_resolver,
                )
            except TextOverflowError as exc:
                raise SignatureTextOverflowError(element.id) from exc
            continue

        text = format_element_value(element, values)
        if text is None:
            continue

        if element.kind == "qr":
            qr_box = _draw_qr_in_box(
                canvas, text, (left, top, right, bottom), element.id, font_resolver,
                show_id=element.show_id is not False,
            )
            if element.source == "verification_url":
                record_qr_region(canvas, text, qr_box)
            continue

        style = element.style
        if style is None:  # pragma: no cover - protected by the layout schema
            raise ImageRenderError("A text element has no style.")
        if element.source == "verification_code":
            _draw_id_text_in_box(canvas, text, (left, top, right, bottom), style, font_resolver)
            continue
        _draw_text_in_box(
            canvas,
            text,
            (left, top, right, bottom),
            style,
            element.id,
            font_resolver,
            wrap=element.kind == "multiline",
        )

    return canvas


def _template_layout(template: Template) -> dict[str, Any]:
    if isinstance(template.layout, dict) and template.layout.get("version") == 2:
        return normalize_layout(template.layout)
    return legacy_positions_to_layout(template.positions, template.dimensions)


def _certificate_values(certificate: Certificate) -> dict[str, Any]:
    values: dict[str, Any] = {
        "recipient_name": certificate.recipient_name,
        "topic": certificate.topic,
        "issue_date": certificate.issue_date,
        "verification_url": _verification_url(certificate.verification_code),
        "verification_code": certificate.verification_code,
        "custom_fields": certificate.custom_fields or {},
    }
    values.update(
        {
            f"custom.{key}": value
            for key, value in (certificate.custom_fields or {}).items()
        }
    )
    return values


def render_certificate(
    template: Template,
    certificate: Certificate,
    output_path: str | Path,
    *,
    signatures: dict[str, PreparedSignature] | None = None,
) -> Path:
    destination = Path(output_path)
    temporary_path: Path | None = None
    published = False

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ImageRenderError(
                "The certificate image destination already exists."
            )

        with Image.open(resolve_template_file(template.background_image_path)) as source:
            background = source.convert("RGB")
        if destination.suffix.lower() == ".webp" and max(background.size) > WEBP_MAX_DIMENSION:
            background.close()
            raise ImageRenderError("WebP supports at most 16383 pixels per side; use a smaller template.")

        db = object_session(certificate) or object_session(template)

        def certificate_font_resolver(
            reference: FontReference, weight: int
        ) -> Path:
            return resolve_font(
                db,
                certificate.organization_id,
                reference,
                weight=weight,
            )

        try:
            canvas = render_layout(
                background,
                _template_layout(template),
                _certificate_values(certificate),
                certificate_font_resolver,
                signatures=signatures,
            )
        finally:
            background.close()

        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=destination.parent,
            prefix=f".{destination.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            save_image(canvas, temporary, file_format="WEBP" if destination.suffix.lower()==".webp" else "PNG", text=issuance_metadata(canvas, certificate.verification_code), compress_level=settings.PNG_COMPRESS_LEVEL, webp_method=settings.WEBP_METHOD)
            temporary.flush()
            os.fsync(temporary.fileno())
        canvas.close()
        os.link(temporary_path, destination)
        published = True
        temporary_path.unlink()
        temporary_path = None
        from services.object_storage import publish_file
        publish_file(destination)
        return destination
    except (ImageRenderError, StorageUnavailableError):
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise
    except (
        FontNotFoundError,
        TemplateLayoutError,
        TextOverflowError,
        SignatureTextOverflowError,
        ValueFormatError,
        StoredFileUnavailableError,
        ThaiShapingUnavailableError,
        ValueError,
    ) as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise ImageRenderError(str(exc)) from exc
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise ImageRenderError() from exc


__all__ = [
    "FontResolver",
    "ImageRenderError",
    "MIN_QR_SIDE_PX",
    "QrTooSmallError",
    "qr_code_side",
    "render_certificate",
    "render_layout",
]
