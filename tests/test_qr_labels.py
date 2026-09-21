from PIL import Image, ImageChops, ImageDraw
import pytest

from models import Certificate
from services.font_registry import BUILTIN_FONTS
from services.image_builder import _certificate_values, render_layout


def qr_layout(box=None, **changes):
    return {"version": 2, "elements": [{
        "id": "verification_qr", "source": "verification_url", "kind": "qr",
        "label": "คิวอาร์โค้ด", "required": True, "visible": True,
        "box": box or {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
        **changes,
    }]}


def font_resolver(*_):
    return BUILTIN_FONTS["sarabun"].regular_path


def caption_ink_bounds(image, region, threshold=128):
    # Inspect final pixels, including captions drawn on an intermediate layer.
    with image.crop(region).convert("L") as footer:
        with footer.point(lambda value: 255 if value < threshold else 0) as ink:
            bounds = ink.getbbox()
    assert bounds is not None
    return (region[0] + bounds[0], region[1] + bounds[1], region[0] + bounds[2], region[1] + bounds[3])


@pytest.fixture
def drawn_labels(monkeypatch):
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        drawn.append((text, draw.textbbox(xy, text, font=kwargs["font"], anchor=kwargs.get("anchor"))))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    return drawn


@pytest.mark.parametrize("code", ["ABC1234567890XYZ", "PREVIEW12345678"])
def test_qr_label_shows_complete_verification_code_centered_inside_box(code, drawn_labels):
    with Image.new("RGB", (400, 400), "#181818") as background:
        with render_layout(background, qr_layout(), {"verification_url": f"https://example.test/?code={code}"}, font_resolver) as rendered:
            assert len(drawn_labels) == 1
            label, _working_bounds = drawn_labels[0]
            assert label == f"ID: {code}"
            bounds = caption_ink_bounds(rendered, (80, 284, 320, 320), threshold=16)
            assert 80 <= bounds[0] < bounds[2] <= 320
            assert 80 <= bounds[1] < bounds[3] <= 320
            assert abs((bounds[0] + bounds[2]) / 2 - 200) <= 2
            assert bounds[1] > 280  # Caption is below the QR, in the footer.
            assert rendered.getpixel((bounds[0], 319)) == (255, 255, 255)
            changed = ImageChops.difference(background, rendered).getbbox()
            assert 80 <= changed[0] < changed[2] <= 320
            assert changed[1] == 80 and changed[3] == 320


def test_qr_label_and_code_stay_inside_block_at_canvas_bottom(drawn_labels):
    box = {"x": 0.6, "y": 0.7, "width": 0.4, "height": 0.3}
    with Image.new("RGB", (400, 400), "white") as background:
        with render_layout(background, qr_layout(box), {"verification_url": "https://example.test/?code=A1B2C3D4E5F6G7H8"}, font_resolver) as rendered:
            assert drawn_labels[0][0] == "ID: A1B2C3D4E5F6G7H8"
            bounds = caption_ink_bounds(rendered, (240, 385, 400, 400))
            assert 240 <= bounds[0] < bounds[2] <= 400
            assert 280 <= bounds[1] < bounds[3] < 400
            assert abs((bounds[0] + bounds[2]) / 2 - 320) <= 2
            assert ImageChops.difference(background, rendered).getbbox()[3] <= 400


def test_qr_with_no_verification_code_does_not_invent_an_id(drawn_labels):
    with Image.new("RGB", (400, 400), "white") as background:
        with render_layout(background, qr_layout(), {"verification_url": "https://example.test/"}, font_resolver):
            assert drawn_labels == []


def test_hidden_qr_does_not_draw_a_label(drawn_labels):
    with Image.new("RGB", (400, 400), "white") as background:
        with render_layout(background, qr_layout(visible=False), {"verification_url": "https://example.test/?code=HIDDEN"}, font_resolver) as rendered:
            assert drawn_labels == []
            assert ImageChops.difference(background, rendered).getbbox() is None


@pytest.mark.parametrize("side", [1, 2, 8, 64])
def test_small_qr_and_caption_fit_without_spilling_outside_block(side):
    box = {"x": 0, "y": 0, "width": side / 400, "height": side / 400}
    with Image.new("RGB", (400, 400), "#181818") as background:
        with render_layout(background, qr_layout(box), {"verification_url": "https://example.test/?code=SMALL"}, font_resolver) as rendered:
            changed = ImageChops.difference(background, rendered).getbbox()
            assert changed is not None
            assert 0 <= changed[0] < changed[2] <= side
            assert 0 <= changed[1] < changed[3] <= side


def test_issued_qr_label_uses_verification_code_not_database_id(drawn_labels):
    certificate = Certificate(id=123, verification_code="PUBLIC1234567890", recipient_name="Recipient", topic="Course", custom_fields={})
    with Image.new("RGB", (400, 400), "white") as background:
        with render_layout(background, qr_layout(), _certificate_values(certificate), font_resolver):
            assert drawn_labels[0][0] == "ID: PUBLIC1234567890"
