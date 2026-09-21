from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import tempfile
from uuid import uuid4
import warnings

from PIL import Image, UnidentifiedImageError

from config import settings
from services.object_storage import publish_file
from services.certificate_images import WEBP_MAX_DIMENSION


MAX_TEMPLATE_IMAGE_BYTES = 10 * 1024 * 1024


class TemplateImageError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    extension: str
    width: int
    height: int


def validate_template_image(data: bytes, filename: str) -> ValidatedImage:
    if not data or len(data) > MAX_TEMPLATE_IMAGE_BYTES:
        raise TemplateImageError("The template image is invalid.")

    supplied_extension = Path(filename or "").suffix.lower()
    expected_format = {
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
    }.get(supplied_extension)
    if expected_format is None:
        raise TemplateImageError("The template image is invalid.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as candidate:
                actual_format = candidate.format
                width, height = candidate.size
                candidate.verify()
            with Image.open(BytesIO(data)) as decoded:
                decoded.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise TemplateImageError("The template image is invalid.") from exc

    if actual_format != expected_format or actual_format not in {"PNG", "JPEG"}:
        raise TemplateImageError("The template image is invalid.")
    if width <= 0 or height <= 0:
        raise TemplateImageError("The template image is invalid.")

    if max(width, height) > WEBP_MAX_DIMENSION:
        raise TemplateImageError("Template dimensions exceed the WebP limit of 16383 pixels.")
    output = BytesIO()
    try:
        with Image.open(BytesIO(data)) as source:
            # Expand palettes/transparency before lossless encoding; keep hidden RGB too.
            mode = "RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB"
            with source.convert(mode) as normalized:
                options = {key: source.info[key] for key in ("icc_profile", "exif") if key in source.info}
                normalized.save(output, format="WEBP", lossless=True, exact=True,
                                quality=100, method=settings.WEBP_METHOD, **options)
    except (OSError, ValueError) as exc:
        raise TemplateImageError("The template image could not be converted.") from exc
    data = output.getvalue()
    extension = ".webp"
    return ValidatedImage(
        data=data,
        extension=extension,
        width=width,
        height=height,
    )


def store_template_image(image: ValidatedImage) -> Path:
    destination_dir = settings.TEMPLATE_DIR.resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid4()}{image.extension}"
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_dir,
            prefix=".upload-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(image.data)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(destination)
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
