from PIL import Image, ImageDraw, ImageFont
from pythainlp.tokenize import word_tokenize
import pytest

from schemas.template_layout import TextStyle
from services.font_registry import BUILTIN_FONTS
import services.text_layout as text_layout_module
from services.text_layout import (
    ThaiShapingUnavailableError,
    layout_engine_for_text,
    layout_text,
)


def _style() -> TextStyle:
    return TextStyle.model_validate(
        {
            "font": {"source": "builtin", "key": "sarabun"},
            "font_size_px": 32,
            "color": "#111111",
            "weight": 400,
            "align": "left",
            "line_height": 1.25,
        }
    )


def test_thai_wrapping_prefers_dictionary_word_boundaries():
    font = ImageFont.truetype(
        BUILTIN_FONTS["sarabun"].regular_path.as_posix(), size=32
    )
    draw = ImageDraw.Draw(Image.new("RGB", (800, 500), "white"))
    text = "มหาวิทยาลัยราชภัฏภูเก็ตมอบเกียรติบัตรแก่ผู้ผ่านการอบรม"
    words = word_tokenize(text, engine="newmm", keep_whitespace=True)
    maximum_width = round(max(font.getlength(word) for word in words)) + 4

    lines = layout_text(
        draw,
        text,
        font,
        (0, 0, maximum_width, 480),
        _style(),
        element_id="thai_sentence",
    )

    word_boundaries = set()
    consumed = 0
    for word in words:
        consumed += len(word)
        word_boundaries.add(consumed)
    line_boundaries = set()
    consumed = 0
    for line in lines:
        consumed += len(line.text)
        line_boundaries.add(consumed)
    assert len(lines) > 1
    assert "".join(line.text for line in lines) == text
    assert line_boundaries.issubset(word_boundaries)


def test_strict_thai_shaping_has_stable_configuration_error(monkeypatch):
    monkeypatch.setattr(text_layout_module.settings, "REQUIRE_RAQM", True)
    monkeypatch.setattr(text_layout_module, "raqm_available", lambda: False)

    with pytest.raises(ThaiShapingUnavailableError):
        layout_engine_for_text("เกียรติบัตรภาษาไทย")


@pytest.mark.skipif(not text_layout_module.raqm_available(), reason="Requires RAQM")
@pytest.mark.parametrize("height", [300, 400, 850, 1131])
def test_legacy_template_boxes_fit_thai_marks_with_raqm(height):
    from services.image_builder import render_layout
    from services.template_layout import legacy_positions_to_layout

    width = round(height * 1.5)
    layout = legacy_positions_to_layout(
        {"name": {"x": 0.5, "y": 0.2}, "issue_date": {"x": 0.5, "y": 0.7}},
        {"width": width, "height": height},
    )
    with Image.new("RGB", (width, height), "white") as background:
        rendered = render_layout(
            background, layout,
            {"recipient_name": "กี่ Ay", "issue_date": "2026-09-07"},
            lambda *_: BUILTIN_FONTS["sarabun"].regular_path,
        )
        assert rendered.getextrema() != background.getextrema()
        rendered.close()
