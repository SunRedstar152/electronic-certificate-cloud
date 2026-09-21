"""Private object storage; database references are independent of the host directory.

Local files in Supabase mode are a bounded, disposable cache, never the source of truth.
Run one application process per container (the default Docker command).
"""
from __future__ import annotations
import json
import logging
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from threading import RLock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from config import settings

PREFIX = "storage://"
MAX_OBJECT_BYTES = 50 * 1024 * 1024
_LOCK = RLock()
_FRESH_SECONDS = 30
logger = logging.getLogger(__name__)

class StorageUnavailableError(RuntimeError):
    pass

class StorageObjectMissingError(FileNotFoundError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward a server secret to a redirect target.


def roots():
    return {"templates": settings.TEMPLATE_DIR.resolve(),
            "certificates": settings.CERTIFICATE_DIR.resolve(),
            "fonts": settings.FONT_DIR.resolve()}


def object_key(path: Path) -> str:
    path = path.resolve()
    for category, root in roots().items():
        if path.is_relative_to(root):
            relative = path.relative_to(root).as_posix()
            key = category + "/" + relative
            validate_key(key)
            return key
    raise ValueError("File is outside managed storage")


def validate_key(key: str):
    parts = key.split("/")
    if len(parts) < 2 or parts[0] not in roots() or any(
        part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
        for part in parts
    ):
        raise ValueError("Unsafe storage reference")


def reference(path: Path) -> str:
    return PREFIX + object_key(path) if settings.STORAGE_BACKEND == "supabase" else path.as_posix()


def reference_path(value: str, configured_root: Path) -> Path:
    key = value[len(PREFIX):]
    validate_key(key)
    category, relative = key.split("/", 1)
    root = roots()[category]
    if root != Path(configured_root).resolve():
        raise ValueError("Storage category mismatch")
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
        raise ValueError("Unsafe storage path")
    return candidate


def _request(method, route, data=None, content_type=None):
    key = settings.SUPABASE_SECRET_KEY
    headers = {"apikey": key}
    # sb_secret_* is an API key, not a JWT. The gateway handles it via apikey.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    if content_type:
        headers["Content-Type"] = content_type
    url = str(settings.SUPABASE_URL).rstrip("/") + "/storage/v1/" + route
    try:
        return build_opener(NoRedirect()).open(Request(url, data=data, headers=headers, method=method),
                                               timeout=settings.STORAGE_TIMEOUT_SECONDS)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096))
        except (ValueError, OSError):
            detail = {}
        if exc.code == 404 or detail.get("code") in {"NoSuchKey", "not_found"}:
            raise StorageObjectMissingError("Stored file not found") from None
        raise StorageUnavailableError("Private storage request failed") from None
    except (URLError, OSError, TimeoutError):
        raise StorageUnavailableError("Private storage is unavailable") from None


def _route(key, authenticated=False):
    validate_key(key)
    return "object/" + ("authenticated/" if authenticated else "") + quote(settings.SUPABASE_STORAGE_BUCKET, safe="") + "/" + quote(key, safe="/")


def verify_storage():
    if settings.STORAGE_BACKEND != "supabase":
        return
    with _request("GET", "bucket/" + quote(settings.SUPABASE_STORAGE_BUCKET, safe="")) as response:
        details = json.loads(response.read(65536))
    if details.get("public") is not False:
        raise StorageUnavailableError("The certificate bucket must be private")


def _make_room(required, exclude):
    limit = settings.STORAGE_CACHE_MB * 1024 * 1024
    if required > MAX_OBJECT_BYTES or required > limit:
        raise StorageUnavailableError("Stored file exceeds the size limit")
    files = [p for root in roots().values() if root.exists() for p in root.rglob("*")
             if p.is_file() and not p.is_symlink() and not p.name.startswith(".")]
    total = sum(p.stat().st_size for p in files)
    # A grace period protects paths already returned to file/stream responses.
    for p in sorted(files, key=lambda p: p.stat().st_mtime):
        if total + required <= limit:
            return
        stat = p.stat()
        if p != exclude and time.time() - stat.st_mtime > 120:
            size = stat.st_size
            p.unlink(missing_ok=True)
            total -= size
    if total + required > limit:
        raise StorageUnavailableError("Storage cache is busy; retry shortly")


def publish_file(path: Path, *, replace=False):
    if settings.STORAGE_BACKEND != "supabase":
        return
    path = path.resolve()
    key = object_key(path)
    size = path.stat().st_size
    if size > MAX_OBJECT_BYTES:
        raise StorageUnavailableError("Stored file exceeds the size limit")
    with _LOCK:
        _make_room(0, path)
        mime = {".webp":"image/webp", ".ttf":"font/ttf", ".otf":"font/otf"}.get(path.suffix, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        with _request("PUT" if replace else "POST", _route(key), path.read_bytes(), mime) as response:
            response.read(65536)
        os.utime(path, None)


def materialize(value: str, configured_root: Path) -> Path:
    path = reference_path(value, configured_root)
    if settings.STORAGE_BACKEND == "local":
        if not path.is_file():
            raise StorageObjectMissingError("Stored file not found")
        return path
    key = value[len(PREFIX):]
    with _LOCK:
        if path.is_file() and time.time() - path.stat().st_mtime < _FRESH_SECONDS:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with _request("GET", _route(key, authenticated=True)) as response:
                # Reserve enough space before downloading; prevents filling an ephemeral disk.
                _make_room(MAX_OBJECT_BYTES, path)
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".download-", delete=False) as output:
                    temporary = Path(output.name)
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_OBJECT_BYTES:
                            raise StorageUnavailableError("Stored file exceeds the size limit")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, path)
            return path
        except StorageObjectMissingError:
            path.unlink(missing_ok=True)
            raise
        except (OSError, TimeoutError):
            raise StorageUnavailableError("Private storage download failed") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def discard_file(path: Path):
    """Clean a successfully published file after a confirmed database rollback."""
    if settings.STORAGE_BACKEND == "supabase":
        key = object_key(path)
        try:
            body = json.dumps({"prefixes": [key]}).encode()
            with _request("DELETE", "object/" + quote(settings.SUPABASE_STORAGE_BUCKET, safe=""), body, "application/json") as response:
                response.read(65536)
        except StorageObjectMissingError:
            pass
        except StorageUnavailableError:
            # Never hide the original DB failure; an orphan can be cleaned after recovery.
            logger.warning("Could not remove an unreferenced storage object after rollback")
    path.unlink(missing_ok=True)
