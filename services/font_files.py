from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fontTools.ttLib import TTFont, TTLibError

from config import settings
from services.object_storage import publish_file


MAX_FONT_BYTES = 5 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".ttf": "ttf", ".otf": "otf"}
THAI_REQUIRED_CODEPOINTS = (
    set(range(0x0E01, 0x0E2F))
    | set(range(0x0E30, 0x0E3B))
    | set(range(0x0E40, 0x0E4F))
    | set(range(0x0E50, 0x0E5A))
)
CERTIFICATE_REQUIRED_CODEPOINTS = THAI_REQUIRED_CODEPOINTS | set(range(0x20, 0x7F))


class FontFileError(ValueError):
    """Raised when an uploaded font file cannot be accepted."""


class FontThaiUnsupportedError(FontFileError):
    """Raised when a valid font does not contain the required Thai glyphs."""


@dataclass(frozen=True)
class ValidatedFont:
    data: bytes
    extension: str
    format: str
    supports_thai: bool
    sha256: str


def _font_format(font: TTFont) -> str:
    if font.sfntVersion == "OTTO":
        return "otf"
    if font.sfntVersion in {"\x00\x01\x00\x00", "true", "typ1"}:
        return "ttf"
    raise FontFileError("Unsupported OpenType container.")


def validate_font_upload(data: bytes, filename: str) -> ValidatedFont:
    extension = Path(filename).suffix.lower()
    expected_format = SUPPORTED_EXTENSIONS.get(extension)
    if expected_format is None:
        raise FontFileError("Only .ttf and .otf font files are supported.")
    if not data:
        raise FontFileError("The font file is empty.")
    if len(data) > MAX_FONT_BYTES:
        raise FontFileError("The font file exceeds the 5 MB limit.")

    try:
        font = TTFont(BytesIO(data), lazy=False)
    except (TTLibError, OSError, ValueError, TypeError) as exc:
        raise FontFileError("The uploaded file is not a valid font.") from exc

    try:
        actual_format = _font_format(font)
        if actual_format != expected_format:
            raise FontFileError("The filename extension does not match the font format.")
        codepoints = {
            codepoint
            for table in font["cmap"].tables
            for codepoint in table.cmap
        }
    except (KeyError, AttributeError, TypeError, TTLibError) as exc:
        raise FontFileError("The uploaded font has an invalid character map.") from exc
    finally:
        font.close()

    if not CERTIFICATE_REQUIRED_CODEPOINTS.issubset(codepoints):
        raise FontThaiUnsupportedError(
            "The uploaded font must contain Thai and basic Latin glyphs."
        )

    return ValidatedFont(
        data=data,
        extension=extension,
        format=actual_format,
        supports_thai=True,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def store_font(validated: ValidatedFont, organization_id: int) -> Path:
    storage_root = Path(settings.FONT_DIR).resolve()
    organization_dir = storage_root / str(organization_id)
    organization_dir.mkdir(parents=True, exist_ok=True)
    destination = organization_dir / f"{uuid4().hex}{validated.extension}"
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".font-",
            suffix=".tmp",
            dir=organization_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(validated.data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    try:
        publish_file(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


__all__ = [
    "MAX_FONT_BYTES",
    "CERTIFICATE_REQUIRED_CODEPOINTS",
    "FontFileError",
    "FontThaiUnsupportedError",
    "ValidatedFont",
    "store_font",
    "validate_font_upload",
]
