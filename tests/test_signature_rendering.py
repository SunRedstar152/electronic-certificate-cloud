from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

import services.image_builder as image_builder_module
from schemas.template_layout import FontReference
from services.image_builder import render_layout
from services.signatures import PreparedSignature


FONT_DIR = Path(__file__).resolve().parents[1] / "static" / "fonts"


def font_resolver(reference: FontReference, weight: int):
    del reference
    return FONT_DIR / ("Sarabun-Bold.ttf" if weight >= 700 else "Sarabun-Regular.ttf")


def signature_layout(*, visible=True, box=None, font_size=28):
    return {
        "version": 2,
        "elements": [{
            "id": "signature", "source": "signature", "kind": "signature",
            "label": "ลายเซ็น", "required": True, "visible": visible,
            "box": box or {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.5},
            "style": {
                "font": {"source": "builtin", "key": "sarabun"},
                "font_size_px": font_size, "color": "#111111", "weight": 400,
                "align": "center", "line_height": 1.2,
            },
        }],
    }


def transparent_signature_image(size=(200, 80)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).line(
        [(10, 60), (60, 15), (100, 65), (190, 30)],
        fill="#111111",
        width=5,
    )
    return image


def prepared_signature(*, signer_name="ชื่อผู้ลงนาม", long_text=False):
    return PreparedSignature(
        image=transparent_signature_image(),
        signer_name=("ชื่อผู้ลงนาม" * 30 if long_text else signer_name),
        signer_position=("ตำแหน่ง" * 30 if long_text else "ตำแหน่ง"),
    )


def test_signature_block_contains_image_name_and_position_inside_60_20_20_regions(monkeypatch):
    signature = PreparedSignature(
        image=transparent_signature_image(),
        signer_name="ผู้ช่วยศาสตราจารย์ ดร. เห็ดศักดิ์ สุพพินดี",
        signer_position="คณบดีคณะครุศาสตร์",
    )
    drawn = []
    real_draw = image_builder_module.ImageDraw.Draw

    class TrackingDraw:
        def __init__(self, image):
            self.delegate = real_draw(image)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def text(self, coordinates, value, **kwargs):
            drawn.append((value, coordinates))
            return self.delegate.text(coordinates, value, **kwargs)

    monkeypatch.setattr(image_builder_module.ImageDraw, "Draw", lambda image: TrackingDraw(image))
    try:
        rendered = render_layout(
            Image.new("RGB", (1000, 1000), "white"),
            signature_layout(),
            {},
            font_resolver,
            signature=signature,
        )
    finally:
        signature.close()

    assert [value for value, _ in drawn] == [
        "(ผู้ช่วยศาสตราจารย์ ดร. เห็ดศักดิ์ สุพพินดี)",
        "คณบดีคณะครุศาสตร์",
    ]
    assert 500 <= drawn[0][1][1] < 600
    assert 600 <= drawn[1][1][1] < 700
    for top, bottom in ((200, 500), (500, 600), (600, 700)):
        region = rendered.crop((200, top, 800, bottom))
        assert ImageChops.difference(Image.new("RGB", region.size, "white"), region).getbbox()
    assert ImageChops.difference(Image.new("RGB", rendered.size, "white"), rendered).getbbox()


def test_signature_image_is_contained_in_top_sixty_percent():
    rendered = render_signature_fixture(image_size=(400, 100), box=(200, 200, 600, 500))
    ink = ImageChops.difference(Image.new("RGB", rendered.size, "white"), rendered).getbbox()
    assert ink is not None
    assert ink[0] >= 200 and ink[2] <= 800
    assert ink[1] >= 200 and ink[3] <= 700


def test_parenthesized_name_is_not_wrapped_twice(monkeypatch):
    drawn = capture_drawn_text(monkeypatch, signer_name="(ชื่อผู้ลงนาม)")
    assert drawn[0] == "(ชื่อผู้ลงนาม)"


def test_position_uses_eighty_five_percent_font_size(monkeypatch):
    sizes = capture_truetype_sizes(monkeypatch, name_font_size=40)
    assert sizes[-2:] == [40, 34]


def test_hidden_signature_draws_nothing():
    background = Image.new("RGB", (1000, 1000), "white")
    signature = prepared_signature()
    try:
        rendered = render_layout(
            background, signature_layout(visible=False), {}, font_resolver, signature=signature
        )
    finally:
        signature.close()
    assert ImageChops.difference(background, rendered).getbbox() is None


def test_signature_text_can_extend_beyond_its_block_without_overlapping(monkeypatch):
    text_bounds = []
    real_text = ImageDraw.ImageDraw.text

    def capture(draw, xy, value, *args, **kwargs):
        text_bounds.append(draw.textbbox(xy, value, font=kwargs["font"], anchor=kwargs["anchor"]))
        return real_text(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    signature = prepared_signature(long_text=True)
    try:
        with Image.new("RGB", (200, 100), "white") as background:
            with render_layout(
                background,
                signature_layout(box={"x": 0, "y": 0, "width": 0.2, "height": 0.2}),
                {}, font_resolver, signature=signature,
            ) as rendered:
                bounds = ImageChops.difference(background, rendered).getbbox()
                assert bounds and bounds[2] > 40 and bounds[3] > 20
    finally:
        signature.close()
    assert len(text_bounds) == 2
    assert text_bounds[0][3] < text_bounds[1][1]


def render_signature_fixture(*, image_size=(200, 80), box=(200, 200, 600, 500)):
    normalized = {
        "x": box[0] / 1000,
        "y": box[1] / 1000,
        "width": box[2] / 1000,
        "height": box[3] / 1000,
    }
    signature = PreparedSignature(transparent_signature_image(image_size), "ชื่อผู้ลงนาม", "ตำแหน่ง")
    try:
        return render_layout(
            Image.new("RGB", (1000, 1000), "white"),
            signature_layout(box=normalized), {}, font_resolver, signature=signature,
        )
    finally:
        signature.close()


def capture_drawn_text(monkeypatch, *, signer_name):
    values = []
    real_draw = image_builder_module.ImageDraw.Draw

    class TrackingDraw:
        def __init__(self, image):
            self.delegate = real_draw(image)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def text(self, coordinates, value, **kwargs):
            values.append(value)
            return self.delegate.text(coordinates, value, **kwargs)

    monkeypatch.setattr(image_builder_module.ImageDraw, "Draw", lambda image: TrackingDraw(image))
    signature = prepared_signature(signer_name=signer_name)
    try:
        render_layout(
            Image.new("RGB", (1000, 1000), "white"),
            signature_layout(), {}, font_resolver, signature=signature,
        )
    finally:
        signature.close()
    return values


def capture_truetype_sizes(monkeypatch, *, name_font_size):
    sizes = []
    real_truetype = image_builder_module.ImageFont.truetype

    def tracking_truetype(path, size, **kwargs):
        sizes.append(size)
        return real_truetype(path, size, **kwargs)

    monkeypatch.setattr(image_builder_module.ImageFont, "truetype", tracking_truetype)
    signature = prepared_signature()
    try:
        render_layout(
            Image.new("RGB", (1000, 1000), "white"),
            signature_layout(font_size=name_font_size), {}, font_resolver, signature=signature,
        )
    finally:
        signature.close()
    return sizes
