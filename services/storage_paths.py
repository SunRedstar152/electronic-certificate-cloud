from __future__ import annotations

from pathlib import Path
from typing import Iterable

from config import settings


APPLICATION_ROOT = Path(__file__).resolve().parents[1]
LEGACY_TEMPLATE_ROOT = APPLICATION_ROOT / "static" / "uploads" / "templates"
LEGACY_FONT_ROOT = APPLICATION_ROOT / "static" / "uploads" / "fonts"


class StoredFileUnavailableError(FileNotFoundError):
    """Raised when a persisted file path is unavailable or outside managed storage."""

    def __init__(self, message: str, *, reason: str = "missing"):
        self.reason = reason
        super().__init__(message)


def storage_root(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate.resolve()


def legacy_certificate_root() -> Path:
    """Return the application's pre-storage-layout certificate root."""

    return APPLICATION_ROOT / "static" / "generated"


def resolve_managed_file(
    stored_path: str | Path,
    configured_root: str | Path,
    *,
    legacy_roots: Iterable[str | Path] = (),
    relative_roots: Iterable[str | Path] = (),
) -> Path:
    from services.object_storage import PREFIX, materialize, StorageObjectMissingError
    if str(stored_path).startswith(PREFIX):
        try:
            return materialize(str(stored_path), Path(configured_root))
        except ValueError:
            raise StoredFileUnavailableError("Unsafe storage reference", reason="unsafe") from None
        except StorageObjectMissingError:
            raise StoredFileUnavailableError("Stored file not found", reason="missing") from None
    root = storage_root(configured_root)
    raw = Path(stored_path)
    candidates = [raw.resolve()]
    if not raw.is_absolute():
        candidates.extend(
            (storage_root(candidate_root) / raw).resolve()
            for candidate_root in (root, *relative_roots)
        )
    allowed_roots = (root, *(storage_root(path) for path in legacy_roots))
    allowed_candidate = False
    for candidate in candidates:
        if any(candidate.is_relative_to(allowed) for allowed in allowed_roots):
            allowed_candidate = True
        if candidate.is_file() and any(candidate.is_relative_to(allowed) for allowed in allowed_roots):
            return candidate
    raise StoredFileUnavailableError(
        "The stored file is unavailable.",
        reason="missing" if allowed_candidate else "unsafe",
    )


def resolve_template_file(stored_path: str | Path) -> Path:
    if str(stored_path) == "builtin:original":
        path = APPLICATION_ROOT / "assets" / "templates" / "original" / "background.webp"
        if not path.is_file():
            raise StoredFileUnavailableError("The bundled template background is missing.", reason="missing")
        return path
    return resolve_managed_file(
        stored_path,
        settings.TEMPLATE_DIR,
        legacy_roots=(LEGACY_TEMPLATE_ROOT,),
    )


def resolve_certificate_file(stored_path: str | Path) -> Path:
    return resolve_managed_file(
        stored_path,
        settings.CERTIFICATE_DIR,
        legacy_roots=(legacy_certificate_root(),),
        relative_roots=(APPLICATION_ROOT,),
    )


__all__ = [
    "APPLICATION_ROOT",
    "LEGACY_TEMPLATE_ROOT",
    "LEGACY_FONT_ROOT",
    "legacy_certificate_root",
    "StoredFileUnavailableError",
    "resolve_managed_file",
    "resolve_certificate_file",
    "resolve_template_file",
    "storage_root",
]
