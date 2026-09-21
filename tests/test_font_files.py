from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from config import settings
from models import FontAsset, Organization, RecordStatus
from schemas.template_layout import FontReference
from services.font_files import (
    CERTIFICATE_REQUIRED_CODEPOINTS,
    MAX_FONT_BYTES,
    FontFileError,
    FontThaiUnsupportedError,
    store_font,
    validate_font_upload,
)
from services.font_registry import (
    BUILTIN_FONTS,
    FontNotFoundError,
    resolve_font,
)


def latin_only_font_bytes() -> bytes:
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    glyphs = {}
    for name in (".notdef", "A"):
        glyphs[name] = TTGlyphPen(None).glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({".notdef": (500, 0), "A": (600, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({0x41: "A"})
    builder.setupNameTable({
        "familyName": "Latin Test",
        "styleName": "Regular",
        "uniqueFontIdentifier": "Latin Test Regular",
        "fullName": "Latin Test Regular",
        "psName": "LatinTest-Regular",
    })
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupPost()
    builder.setupMaxp()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def test_builtin_registry_contains_requested_thai_families():
    assert set(BUILTIN_FONTS) == {
        "sarabun",
        "noto_sans_thai",
        "th_niramit_as",
    }
    assert all(
        font.regular_path.is_file() and font.bold_path.is_file()
        for font in BUILTIN_FONTS.values()
    )


@pytest.mark.parametrize(
    "font_path",
    [
        face
        for family in BUILTIN_FONTS.values()
        for face in (family.regular_path, family.bold_path)
    ],
    ids=lambda path: path.name,
)
def test_builtin_fonts_cover_thai_numbers_and_basic_latin(font_path):
    font = TTFont(font_path)
    try:
        codepoints = {
            codepoint
            for table in font["cmap"].tables
            for codepoint in table.cmap
        }
    finally:
        font.close()

    assert CERTIFICATE_REQUIRED_CODEPOINTS.issubset(codepoints)


@pytest.mark.parametrize(
    "font_key",
    ["sarabun", "noto_sans_thai", "th_niramit_as"],
)
def test_validate_font_upload_accepts_builtin_thai_fonts(font_key):
    path = BUILTIN_FONTS[font_key].regular_path

    validated = validate_font_upload(path.read_bytes(), path.name)

    assert validated.format in {"ttf", "otf"}
    assert validated.supports_thai is True
    assert len(validated.sha256) == 64


def test_validate_font_upload_rejects_font_without_thai_glyphs():
    with pytest.raises(FontThaiUnsupportedError):
        validate_font_upload(latin_only_font_bytes(), "latin.ttf")


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (b"not a font", "broken.ttf"),
        (b"x" * (MAX_FONT_BYTES + 1), "large.ttf"),
        (b"not a font", "font.woff"),
    ],
    ids=["corrupt", "oversize", "unsupported-extension"],
)
def test_validate_font_upload_rejects_invalid_files(data, filename):
    with pytest.raises(FontFileError):
        validate_font_upload(data, filename)


def test_store_font_uses_owned_uuid_path_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "FONT_DIR", tmp_path)
    source = BUILTIN_FONTS["sarabun"].regular_path
    validated = validate_font_upload(source.read_bytes(), source.name)

    stored = store_font(validated, organization_id=17)

    assert stored.parent == tmp_path / "17"
    assert stored.suffix == source.suffix.lower()
    UUID(stored.stem)
    assert stored.read_bytes() == source.read_bytes()
    assert list(stored.parent.iterdir()) == [stored]


def test_resolve_builtin_font_uses_bold_face_for_bold_weight():
    resolved = resolve_font(
        None,
        7,
        FontReference(source="builtin", key="sarabun"),
        weight=700,
    )

    assert resolved == BUILTIN_FONTS["sarabun"].bold_path.resolve()


def test_resolve_uploaded_font_is_scoped_to_organization(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "FONT_DIR", tmp_path)
    first = Organization(name="First Fonts", code="FIRST-FONTS")
    second = Organization(name="Second Fonts", code="SECOND-FONTS")
    db_session.add_all([first, second])
    db_session.flush()
    owned_path = tmp_path / str(first.id) / "owned.ttf"
    owned_path.parent.mkdir(parents=True)
    owned_path.write_bytes(BUILTIN_FONTS["sarabun"].regular_path.read_bytes())
    asset = FontAsset(
        organization_id=first.id,
        display_name="Owned Thai Font",
        original_filename="owned.ttf",
        file_path=owned_path.as_posix(),
        sha256="c" * 64,
        format="ttf",
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(asset)
    db_session.flush()

    assert resolve_font(
        db_session,
        first.id,
        FontReference(source="uploaded", id=asset.id),
    ) == owned_path.resolve()
    with pytest.raises(FontNotFoundError):
        resolve_font(
            db_session,
            second.id,
            FontReference(source="uploaded", id=asset.id),
        )


def test_resolve_uploaded_font_rejects_path_outside_font_storage(
    db_session, tmp_path, monkeypatch
):
    storage = tmp_path / "storage"
    monkeypatch.setattr(settings, "FONT_DIR", storage)
    organization = Organization(name="Unsafe Font Org", code="UNSAFE-FONT")
    db_session.add(organization)
    db_session.flush()
    outside = tmp_path / "outside.ttf"
    outside.write_bytes(BUILTIN_FONTS["sarabun"].regular_path.read_bytes())
    asset = FontAsset(
        organization_id=organization.id,
        display_name="Unsafe Font",
        original_filename="unsafe.ttf",
        file_path=outside.as_posix(),
        sha256="d" * 64,
        format="ttf",
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(asset)
    db_session.flush()

    with pytest.raises(FontNotFoundError):
        resolve_font(
            db_session,
            organization.id,
            FontReference(source="uploaded", id=asset.id),
        )


def test_relative_font_root_round_trips_stored_path(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "FONT_DIR", Path("storage/fonts"))
    organization = Organization(name="Relative Font Org", code="RELATIVE-FONT")
    db_session.add(organization)
    db_session.flush()
    validated = validate_font_upload(
        BUILTIN_FONTS["sarabun"].regular_path.read_bytes(), "relative.ttf"
    )
    stored = store_font(validated, organization.id)
    asset = FontAsset(
        organization_id=organization.id,
        display_name="Relative Thai Font",
        original_filename="relative.ttf",
        file_path=stored.as_posix(),
        sha256=validated.sha256,
        format=validated.format,
        supports_thai=True,
        status=RecordStatus.active,
    )
    db_session.add(asset)
    db_session.flush()

    assert stored.is_absolute()
    assert resolve_font(
        db_session,
        organization.id,
        FontReference(source="uploaded", id=asset.id),
    ) == stored.resolve()
