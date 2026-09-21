"""Lossless, independently verified QR replacement without re-rendering a certificate.

Coordinates live in the PNG itself. Legacy bounds are accepted only when the
decoded QR matches pixels produced by the original renderer exactly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit

from PIL import Image, ImageChops, PngImagePlugin
import qrcode
from qrcode.exceptions import DataOverflowError
import zxingcpp


from services.certificate_images import image_text, image_options, save_image


METADATA_KEY = "pkru.verification_qr"


class QrUpdateError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def verification_url(base_url: str, code: str) -> str:
    base = str(base_url).strip().rstrip("/")
    try:
        parts = urlsplit(base)
        valid = (
            parts.scheme in {"http", "https"} and parts.hostname
            and not parts.username and not parts.password
            and not parts.query and not parts.fragment
            and "?" not in base and "#" not in base
            and len(base) <= 2048 and not any(c.isspace() for c in base)
        )
        # Accessing port also validates malformed port numbers.
        parts.port
    except ValueError:
        valid = False
    if not valid or not code:
        raise QrUpdateError("invalid_base_url")
    return f"{base}/?{urlencode({'code': code})}"


def _has_code(url: str, code: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and parse_qs(parts.query).get("code") == [code]
    except ValueError:
        return False


def record_qr_region(image: Image.Image, url: str, box: tuple[int, int, int, int]) -> None:
    """Called by the renderer after drawing a verification QR square."""
    regions = image.info.setdefault("_pkru_qr_regions", [])
    regions.append({"box": list(box), "url": url})


def issuance_metadata(image: Image.Image, code: str) -> dict[str,str]:
    return {METADATA_KEY: json.dumps({
        "version": 1, "code": code, "regions": image.info.get("_pkru_qr_regions", []),
    }, separators=(",", ":"))}


def issuance_png_info(image: Image.Image, code: str) -> PngImagePlugin.PngInfo:
    info = PngImagePlugin.PngInfo()
    for key,value in issuance_metadata(image,code).items():
        info.add_text(key,value)
    return info


def _read_qrs(image: Image.Image):
    return zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)


def _qr_image(url: str, side: int, *, require_readable: bool = False) -> Image.Image:
    try:
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.make(fit=True)
        if require_readable and side < (qr.modules_count + qr.border * 2) * 2:
            raise QrUpdateError("qr_too_small_for_url")
        with qr.make_image().convert("RGB") as source:
            return source.resize((side, side), Image.Resampling.NEAREST)
    except (DataOverflowError, OverflowError):
        raise QrUpdateError("qr_url_too_long") from None


def _same_pixels(a: Image.Image, b: Image.Image) -> bool:
    if a.size != b.size or a.mode != b.mode:
        return False
    with ImageChops.difference(a, b) as difference:
        return difference.getbbox() is None


def _validate_regions(image: Image.Image, code: str, metadata: dict) -> list[dict]:
    if metadata.get("version") != 1 or metadata.get("code") != code:
        raise QrUpdateError("qr_metadata_mismatch")
    regions = metadata.get("regions")
    if not isinstance(regions, list) or len(regions) > 16:
        raise QrUpdateError("qr_metadata_invalid")
    boxes = []
    for region in regions:
        if not isinstance(region, dict):
            raise QrUpdateError("qr_metadata_invalid")
        box, url = region.get("box"), region.get("url")
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
            raise QrUpdateError("qr_metadata_invalid")
        left, top, right, bottom = box
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height and right-left == bottom-top):
            raise QrUpdateError("qr_bounds_invalid")
        if not isinstance(url, str) or len(url) > 4096 or not _has_code(url, code):
            raise QrUpdateError("qr_code_mismatch")
        for x1, y1, x2, y2 in boxes:
            if max(left, x1) < min(right, x2) and max(top, y1) < min(bottom, y2):
                raise QrUpdateError("qr_bounds_overlap")
        boxes.append(box)
        with image.crop(box) as actual, _qr_image(url, right-left) as expected:
            if not _same_pixels(actual, expected):
                raise QrUpdateError("qr_pixels_mismatch")
    return regions


def _legacy_box(image: Image.Image, barcode) -> list[int]:
    points = [barcode.position.top_left, barcode.position.top_right, barcode.position.bottom_right, barcode.position.bottom_left]
    if not (abs(points[0].y-points[1].y) <= 1 and abs(points[1].x-points[2].x) <= 1
            and abs(points[2].y-points[3].y) <= 1 and abs(points[3].x-points[0].x) <= 1):
        raise QrUpdateError("qr_bounds_unsupported")
    qr = qrcode.QRCode()
    qr.add_data(barcode.text)
    qr.make(fit=True)
    modules, total = qr.modules_count, qr.modules_count + qr.border * 2
    symbol_left, symbol_top = min(p.x for p in points), min(p.y for p in points)
    symbol_width = max(p.x for p in points)-symbol_left
    approximate_side = round(symbol_width*total/modules)
    matches = []
    for side in range(max(1, approximate_side-4), approximate_side+5):
        quiet = round(side*qr.border/total)
        with _qr_image(barcode.text, side) as expected:
            for left in range(symbol_left-quiet-2, symbol_left-quiet+3):
                for top in range(symbol_top-quiet-2, symbol_top-quiet+3):
                    if left < 0 or top < 0 or left+side > image.width or top+side > image.height:
                        continue
                    box = [left, top, left+side, top+side]
                    with image.crop(box) as actual:
                        if _same_pixels(actual, expected):
                            matches.append(box)
    if len(matches) != 1:
        raise QrUpdateError("qr_bounds_ambiguous" if matches else "qr_pixels_unrecognized")
    return matches[0]


def _regions(image: Image.Image, code: str) -> tuple[list[dict], bool]:
    try:
        raw = image_text(image).get(METADATA_KEY)
    except (ValueError, TypeError):
        raise QrUpdateError("qr_metadata_invalid") from None
    if raw is not None:
        try:
            if len(raw) > 65536:
                raise ValueError()
            metadata = json.loads(raw)
            if not isinstance(metadata, dict):
                raise ValueError()
        except (TypeError, ValueError):
            raise QrUpdateError("qr_metadata_invalid") from None
        return _validate_regions(image, code, metadata), False
    decoded = [r for r in _read_qrs(image) if _has_code(r.text, code)]
    if not decoded:
        raise QrUpdateError("qr_not_found")
    regions = [{"box": _legacy_box(image, result), "url": result.text} for result in decoded]
    return _validate_regions(image, code, {"version": 1, "code": code, "regions": regions}), True


def _file_identity(path: Path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise QrUpdateError("image_not_regular")
    return value


def _unchanged_file(before, after) -> bool:
    return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)


def _verify_result(before: Image.Image, after: Image.Image, regions: list[dict], target: str) -> None:
    if after.mode != before.mode or after.size != before.size:
        raise QrUpdateError("image_changed")
    with ImageChops.difference(before, after) as difference:
        for region in regions:
            box = region["box"]
            with after.crop(box) as crop:
                if [r.text for r in _read_qrs(crop)] != [target]:
                    raise QrUpdateError("qr_verification_failed")
            difference.paste((0, 0, 0), box)
        if difference.getbbox() is not None:
            raise QrUpdateError("outside_pixels_changed")
    # Also scan the whole certificate: nearby graphics must not prevent reading.
    if regions and sum(r.text == target for r in _read_qrs(after)) != len(regions):
        raise QrUpdateError("qr_verification_failed")


def update_certificate_qr(path: Path, code: str, base_url: str, *, dry_run: bool = False, compress_level: int = 9, webp_method: int = 4) -> str:
    """Return updated/indexed/unchanged; dry-run performs validation without writes."""
    if not 0 <= compress_level <= 9:
        raise ValueError("compress_level must be between 0 and 9")
    target = verification_url(base_url, code)
    path = Path(path)
    staged = None
    try:
        before_stat = _file_identity(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if not _unchanged_file(before_stat, os.fstat(handle.fileno())):
                raise QrUpdateError("source_changed")
            with Image.open(handle) as original:
                if original.format not in {"PNG", "WEBP"} or original.mode != "RGB":
                    raise QrUpdateError("image_format_unsupported")
                original.load()
                regions, legacy = _regions(original, code)
                changed = any(region["url"] != target for region in regions)
                if not changed and not legacy:
                    return "unchanged"
                status = "updated" if changed else "indexed"
                with original.copy() as updated:
                    new_regions = []
                    for region in regions:
                        box = region["box"]
                        if region["url"] != target:
                            with _qr_image(target, box[2]-box[0], require_readable=True) as qr:
                                updated.paste(qr, box[:2])
                        new_regions.append({"box": box, "url": target})
                    _verify_result(original, updated, new_regions, target)
                    if dry_run:
                        return status
                    metadata = image_text(original)
                    metadata[METADATA_KEY] = json.dumps({"version": 1, "code": code, "regions": new_regions}, separators=(",", ":"))
                    options = image_options(original)
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".qr-update-", suffix=".tmp", delete=False) as temporary:
                        staged = Path(temporary.name)
                        save_image(updated, temporary, file_format=original.format, text=metadata, compress_level=compress_level, webp_method=webp_method, options=options)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    with Image.open(staged) as check:
                        check.load()
                        _verify_result(original, check, new_regions, target)
                        if image_text(check) != metadata:
                            raise QrUpdateError("qr_metadata_changed")
        if not _unchanged_file(before_stat, _file_identity(path)):
            raise QrUpdateError("source_changed")
        os.chmod(staged, stat.S_IMODE(before_stat.st_mode))
        os.replace(staged, path)
        staged = None
        return status
    except QrUpdateError:
        raise
    except (OSError, ValueError, TypeError, KeyError, DataOverflowError):
        raise QrUpdateError("qr_update_failed") from None
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                # A locked temporary file must not hide the controlled failure
                # or stop the batch from reporting/processing later records.
                pass
