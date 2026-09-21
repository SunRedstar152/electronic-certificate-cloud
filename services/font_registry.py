from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from config import settings
from models import FontAsset, RecordStatus
from schemas.template_layout import FontReference
from services.storage_paths import (
    LEGACY_FONT_ROOT,
    StoredFileUnavailableError,
    resolve_managed_file,
)


class FontNotFoundError(LookupError):
    """Raised when a layout references a font unavailable to the organization."""


@dataclass(frozen=True)
class BuiltinFont:
    key: str
    display_name: str
    regular_path: Path
    bold_path: Path


FONT_ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "fonts"
BUILTIN_FONTS = {
    "sarabun": BuiltinFont(
        key="sarabun",
        display_name="Sarabun",
        regular_path=FONT_ASSET_DIR / "Sarabun-Regular.ttf",
        bold_path=FONT_ASSET_DIR / "Sarabun-Bold.ttf",
    ),
    "noto_sans_thai": BuiltinFont(
        key="noto_sans_thai",
        display_name="Noto Sans Thai",
        regular_path=FONT_ASSET_DIR / "NotoSansThai-Regular.ttf",
        bold_path=FONT_ASSET_DIR / "NotoSansThai-Bold.ttf",
    ),
    "th_niramit_as": BuiltinFont(
        key="th_niramit_as",
        display_name="TH Niramit AS",
        regular_path=FONT_ASSET_DIR / "THNiramitAS-Regular.ttf",
        bold_path=FONT_ASSET_DIR / "THNiramitAS-Bold.ttf",
    ),
}


def _resolve_uploaded_path(file_path: str) -> Path:
    try:
        return resolve_managed_file(
            file_path,
            settings.FONT_DIR,
            legacy_roots=(LEGACY_FONT_ROOT,),
        )
    except StoredFileUnavailableError as exc:
        raise FontNotFoundError("The uploaded font file is unavailable.") from exc


def resolve_font(
    db: Session | None,
    organization_id: int,
    reference: FontReference,
    *,
    weight: int = 400,
) -> Path:
    if reference.source == "builtin":
        builtin = BUILTIN_FONTS.get(reference.key or "")
        if builtin is None:
            raise FontNotFoundError("The built-in font does not exist.")
        selected = builtin.bold_path if weight >= 600 else builtin.regular_path
        resolved = selected.resolve()
        if not resolved.is_file():
            raise FontNotFoundError("The built-in font file is unavailable.")
        return resolved

    if db is None or reference.id is None:
        raise FontNotFoundError("A database session is required for uploaded fonts.")
    asset = (
        db.query(FontAsset)
        .filter(
            FontAsset.id == reference.id,
            FontAsset.organization_id == organization_id,
            FontAsset.status == RecordStatus.active,
            FontAsset.supports_thai.is_(True),
        )
        .one_or_none()
    )
    if asset is None:
        raise FontNotFoundError("The uploaded font is unavailable to this organization.")
    return _resolve_uploaded_path(asset.file_path)


__all__ = [
    "BUILTIN_FONTS",
    "BuiltinFont",
    "FontNotFoundError",
    "resolve_font",
]
