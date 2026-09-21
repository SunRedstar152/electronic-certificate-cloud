from datetime import date

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from schemas.template_layout import TemplateElement, TemplateLayout
from services.font_registry import BUILTIN_FONTS
from services.image_builder import render_layout
from services.text_layout import TextOverflowError, layout_text
from services.value_formatter import ValueFormatError, format_element_value


def text_element(
    *,
    element_id="training_period",
    source="custom.training_period",
    kind="date_range",
    font_key="sarabun",
    box=None,
    prefix="ระหว่างวันที่ ",
    separator=" ถึง ",
    suffix="",
):
    value = {
        "id": element_id,
        "source": source,
        "kind": kind,
        "label": "ระยะเวลาการอบรม",
        "required": False,
        "visible": True,
        "box": box or {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.25},
        "style": {
            "font": {"source": "builtin", "key": font_key},
            "font_size_px": 32,
            "color": "#111111",
            "weight": 400,
            "align": "center",
            "line_height": 1.25,
        },
        "format": {
            "locale": "th-TH",
            "calendar": "buddhist",
            "pattern": "long",
            "prefix": prefix,
            "separator": separator,
            "suffix": suffix,
        },
    }
    if kind in {"text", "multiline"}:
        value.pop("format")
    return TemplateElement.model_validate(value)


def test_buddhist_long_date_range_is_thai():
    element = text_element()

    text = format_element_value(
        element,
        {
            "custom.training_period": {
                "start": "2026-08-13",
                "end": "2026-08-15",
            }
        },
    )

    assert text == "ระหว่างวันที่ 13 สิงหาคม 2569 ถึง 15 สิงหาคม 2569"


def test_gregorian_numeric_date_preserves_iso_compatibility():
    element = TemplateElement.model_validate(
        {
            **text_element(
                element_id="issue_date",
                source="issue_date",
                kind="date",
            ).model_dump(),
            "format": {
                "locale": "th-TH",
                "calendar": "gregorian",
                "pattern": "numeric",
                "prefix": "",
                "separator": " ถึง ",
                "suffix": "",
            },
        }
    )

    assert format_element_value(element, {"issue_date": date(2026, 8, 13)}) == "2026-08-13"


def test_time_range_is_deterministic_and_rejects_reverse_range():
    element = text_element(kind="time_range", prefix="เวลา ", suffix=" น.")

    assert format_element_value(
        element,
        {"custom.training_period": {"start": "09:05", "end": "16:30"}},
    ) == "เวลา 09:05 ถึง 16:30 น."

    with pytest.raises(ValueFormatError):
        format_element_value(
            element,
            {"custom.training_period": {"start": "16:30", "end": "09:05"}},
        )


def test_optional_missing_value_is_not_rendered():
    assert format_element_value(text_element(), {}) is None


def test_thai_text_layout_wraps_and_raises_for_vertical_overflow():
    font = ImageFont.truetype(
        BUILTIN_FONTS["sarabun"].regular_path.as_posix(), size=32
    )
    draw = ImageDraw.Draw(Image.new("RGB", (300, 160), "white"))
    element = text_element(
        kind="multiline",
        box={"x": 0.0, "y": 0.0, "width": 0.3, "height": 0.2},
    )

    with pytest.raises(TextOverflowError) as error:
        layout_text(
            draw,
            "เกียรติบัตรสำหรับผู้เข้าร่วม กำลังศึกษา",
            font,
            (0, 0, 90, 32),
            element.style,
            element_id=element.id,
        )

    assert error.value.element_id == element.id


def font_resolver(reference, weight):
    font = BUILTIN_FONTS[reference.key]
    return font.bold_path if weight >= 600 else font.regular_path


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_single_line_text_can_extend_beyond_its_block_without_shrinking(align, monkeypatch):
    element = text_element(kind="text", box={"x": 0.45, "y": 0.2, "width": 0.1, "height": 0.025})
    element.style.align = align
    captured = []
    original = ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        captured.append((text, kwargs["font"].size, kwargs["anchor"], xy))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    value = "ข้อความยาวเกินกรอบ ABCDEFGHIJ"
    with Image.new("RGB", (1000, 400), "white") as background:
        with render_layout(background, TemplateLayout(version=2, elements=[element]), {element.source: value}, font_resolver) as rendered:
            bounds = ImageChops.difference(background, rendered).getbbox()
            assert bounds and (bounds[0] < 450 or bounds[2] > 550)
            assert bounds[3] > 90
    assert len(captured) == 1 and captured[0][0] == value
    assert captured[0][1] == element.style.font_size_px
    assert captured[0][2] == {"left": "lt", "center": "mt", "right": "rt"}[align]


def test_multiline_text_continues_below_its_block():
    element = text_element(kind="multiline", box={"x": 0.1, "y": 0.1, "width": 0.65, "height": 0.025})
    with Image.new("RGB", (800, 500), "white") as background:
        with render_layout(background, TemplateLayout(version=2, elements=[element]), {element.source: "บรรทัดแรก\nบรรทัดที่สอง\nบรรทัดที่สาม"}, font_resolver) as rendered:
            bounds = ImageChops.difference(background, rendered).getbbox()
            assert bounds and bounds[3] > 120
            assert bounds[0] >= 80 and bounds[2] <= 600


def test_multiline_block_narrower_than_a_character_still_renders():
    element = text_element(kind="multiline", box={"x": 0.4, "y": 0.1, "width": 0.001, "height": 0.001})
    with Image.new("RGB", (800, 500), "white") as background:
        with render_layout(background, TemplateLayout(version=2, elements=[element]), {element.source: "กี่AB"}, font_resolver) as rendered:
            assert ImageChops.difference(background, rendered).getbbox()


def test_whitespace_only_multiline_value_renders_as_blank():
    element = text_element(kind="multiline", box={"x": 0.1, "y": 0.1, "width": 0.01, "height": 0.01})
    with Image.new("RGB", (800, 500), "white") as background:
        with render_layout(background, TemplateLayout(version=2, elements=[element]), {element.source: " \t \n   "}, font_resolver) as rendered:
            assert ImageChops.difference(background, rendered).getbbox() is None


@pytest.mark.parametrize(
    "font_key", ["sarabun", "noto_sans_thai", "th_niramit_as"]
)
def test_render_layout_draws_thai_with_every_builtin_inside_element_box(font_key):
    background = Image.new("RGB", (800, 400), "white")
    element = text_element(
        element_id="message",
        source="custom.message",
        kind="multiline",
        font_key=font_key,
        box={"x": 0.2, "y": 0.25, "width": 0.6, "height": 0.5},
    )
    layout = TemplateLayout(version=2, elements=[element])

    rendered = render_layout(
        background,
        layout,
        {"custom.message": "เกียรติบัตรสำหรับผู้เข้าร่วม กำลังศึกษา"},
        font_resolver,
    )

    difference = ImageChops.difference(background, rendered)
    assert difference.getbbox() is not None
    assert difference.getbbox()[0] >= 160
    assert difference.getbbox()[1] >= 100
    assert difference.getbbox()[2] <= 640
    assert difference.getbbox()[3] <= 300


def test_render_layout_keeps_qr_pixels_within_exact_box():
    background = Image.new("RGB", (400, 300), "white")
    layout = TemplateLayout.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "id": "verification_qr",
                    "source": "verification_url",
                    "kind": "qr",
                    "label": "คิวอาร์โค้ด",
                    "required": True,
                    "visible": True,
                    "box": {"x": 0.25, "y": 0.2, "width": 0.25, "height": 1 / 3},
                }
            ],
        }
    )

    rendered = render_layout(
        background,
        layout,
        {"verification_url": "https://certificates.example/?code=ABC123"},
        font_resolver,
    )

    difference = ImageChops.difference(background, rendered)
    bounds = difference.getbbox()
    assert bounds is not None
    assert bounds[0] >= 100
    assert bounds[1] >= 60
    assert bounds[2] <= 200
    assert bounds[3] <= 160


def test_render_layout_centers_square_qr_without_distorting_rectangular_box():
    background = Image.new("RGB", (400, 300), "white")
    layout = TemplateLayout.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "id": "verification_qr",
                    "source": "verification_url",
                    "kind": "qr",
                    "label": "คิวอาร์โค้ด",
                    "required": True,
                    "visible": True,
                    "box": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.3},
                }
            ],
        }
    )

    rendered = render_layout(
        background,
        layout,
        {"verification_url": "https://certificates.example/"},
        font_resolver,
    )

    bounds = ImageChops.difference(background, rendered).getbbox()
    assert bounds is not None
    assert bounds[0] >= 115
    assert bounds[1] >= 60
    assert bounds[2] <= 205
    assert bounds[3] <= 150
    assert (bounds[2] - bounds[0]) == (bounds[3] - bounds[1])


def test_render_layout_fits_tiny_qr_inside_its_box():
    background = Image.new("RGB", (400, 300), "white")
    layout = TemplateLayout.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "id": "tiny_qr",
                    "source": "verification_url",
                    "kind": "qr",
                    "label": "คิวอาร์โค้ด",
                    "required": True,
                    "visible": True,
                    "box": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                }
            ],
        }
    )

    with render_layout(
        background,
        layout,
        {"verification_url": "https://certificates.example/?code=TINY"},
        font_resolver,
    ) as rendered:
        bounds = ImageChops.difference(background, rendered).getbbox()
        assert bounds is not None
        assert 40 <= bounds[0] < bounds[2] <= 80
        assert 30 <= bounds[1] < bounds[3] <= 60


def test_thai_wrapping_never_splits_combining_mark_graphemes():
    font = ImageFont.truetype(
        BUILTIN_FONTS["sarabun"].regular_path.as_posix(), size=32
    )
    draw = ImageDraw.Draw(Image.new("RGB", (300, 300), "white"))
    element = text_element(
        kind="multiline",
        box={"x": 0.0, "y": 0.0, "width": 0.3, "height": 0.8},
    )
    text = "กี่กี่กี่กี่"

    lines = layout_text(
        draw,
        text,
        font,
        (0, 0, 70, 250),
        element.style,
        element_id=element.id,
    )

    assert "".join(line.text for line in lines) == text
    assert all(not line.text.startswith(("ี", "่")) for line in lines)
