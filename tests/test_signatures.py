import base64
from io import BytesIO

import pytest
from PIL import Image

from schemas.signatures import SignatureSubmission
from schemas.template_layout import TemplateLayout
from services.signatures import (
    MAX_SIGNATURE_BYTES,
    SignatureEmptyError,
    SignatureInvalidPngError,
    SignatureRequiredError,
    SignatureTooLargeError,
    SignatureTransparencyRequiredError,
    layout_requires_signature,
    prepare_signature,
    prepare_signature_for_layout,
    prepare_signatures_for_layout,
    close_prepared_signatures,
    SignatureUnexpectedError,
)


def png_data_url(image: Image.Image) -> str:
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=None)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def submission(image: Image.Image) -> SignatureSubmission:
    return SignatureSubmission(
        png_data_url=png_data_url(image),
        signer_name="  ผู้ช่วยศาสตราจารย์ ดร. เห็ดศักดิ์ สุพพินดี  ",
        signer_position="  คณบดีคณะครุศาสตร์  ",
    )


def signature_layout():
    return {
        "version": 2,
        "elements": [{
            "id": "signature", "source": "signature", "kind": "signature",
            "label": "ลายเซ็น", "required": True, "visible": True,
            "box": {"x": 0.35, "y": 0.66, "width": 0.3, "height": 0.24},
            "style": {
                "font": {"source": "builtin", "key": "sarabun"},
                "font_size_px": 28, "color": "#111111", "weight": 400,
                "align": "center", "line_height": 1.2,
            },
        }],
    }


def test_multiple_signatures_reject_unknown_ids_and_ambiguous_legacy_input():
    from copy import deepcopy

    layout = signature_layout()
    second = deepcopy(layout["elements"][0])
    second["id"] = "signature_2"
    layout["elements"].append(second)
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    image.putpixel((5, 5), (20, 20, 20, 255))
    value = submission(image)
    complete = {"signature": value, "signature_2": value}
    with pytest.raises(SignatureRequiredError):
        prepare_signatures_for_layout(layout, {"signature": value})
    with pytest.raises(SignatureUnexpectedError):
        prepare_signatures_for_layout(layout, {**complete, "unknown": value})
    with pytest.raises(SignatureUnexpectedError):
        prepare_signatures_for_layout(layout, complete, single_submission=value)


def test_multiple_signatures_map_by_id_and_ignore_hidden_blocks():
    from copy import deepcopy

    layout = signature_layout()
    second = deepcopy(layout["elements"][0])
    second.update(id="signature_2", visible=False)
    layout["elements"].append(second)
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    image.putpixel((5, 5), (20, 20, 20, 255))
    value = submission(image)
    prepared = prepare_signatures_for_layout(layout, {"signature": value})
    try:
        assert list(prepared) == ["signature"]
        assert prepared["signature"].signer_name == value.signer_name
    finally:
        close_prepared_signatures(prepared)


def test_prepare_signature_crops_transparent_padding_and_trims_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    for x in range(25, 75):
        for y in range(15, 35):
            image.putpixel((x, y), (20, 20, 20, 255))

    prepared = prepare_signature(submission(image))
    try:
        assert prepared.image.mode == "RGBA"
        assert prepared.image.size == (50, 20)
        assert prepared.signer_name == "ผู้ช่วยศาสตราจารย์ ดร. เห็ดศักดิ์ สุพพินดี"
        assert prepared.signer_position == "คณบดีคณะครุศาสตร์"
        assert list(tmp_path.iterdir()) == []
    finally:
        prepared.close()


def test_prepare_signature_rejects_opaque_empty_and_oversized_pngs():
    with pytest.raises(SignatureTransparencyRequiredError):
        prepare_signature(submission(Image.new("RGB", (50, 20), "white")))
    with pytest.raises(SignatureEmptyError):
        prepare_signature(submission(Image.new("RGBA", (50, 20), (0, 0, 0, 0))))
    oversized = "data:image/png;base64," + base64.b64encode(b"x" * (MAX_SIGNATURE_BYTES + 1)).decode()
    with pytest.raises(SignatureTooLargeError):
        prepare_signature(SignatureSubmission(
            png_data_url=oversized, signer_name="ผู้ลงนาม", signer_position="ตำแหน่ง"
        ))


def test_signature_is_required_only_by_a_visible_signature_layout():
    with pytest.raises(SignatureRequiredError):
        prepare_signature_for_layout(signature_layout(), None)
    with pytest.raises(SignatureRequiredError):
        prepare_signature_for_layout(
            signature_layout(), SignatureSubmission(signer_name="ผู้ลงนาม")
        )
    assert layout_requires_signature(TemplateLayout.model_validate(signature_layout()))
    assert prepare_signature_for_layout({"version": 2, "elements": []}, None) is None


@pytest.mark.parametrize(
    "data_url",
    [
        "data:image/jpeg;base64,AAAA",
        "data:image/png;base64,%%%not-base64%%%",
        "data:image/png;base64," + base64.b64encode(b"not a png").decode("ascii"),
    ],
)
def test_prepare_signature_rejects_invalid_data_urls(data_url):
    with pytest.raises(SignatureInvalidPngError):
        prepare_signature(SignatureSubmission(
            png_data_url=data_url,
            signer_name="ผู้ลงนาม",
            signer_position="ตำแหน่ง",
        ))


def test_prepare_signature_rejects_dimensions_over_the_limit():
    with pytest.raises(SignatureTooLargeError):
        prepare_signature(submission(Image.new("RGBA", (4097, 1), (0, 0, 0, 0))))


def test_prepare_signature_accepts_palette_transparency_and_close_releases_image():
    image = Image.new("P", (4, 2), 0)
    image.putpalette([255, 255, 255, 0, 0, 0] + [0, 0, 0] * 254)
    image.info["transparency"] = 0
    image.putpixel((1, 1), 1)
    prepared = prepare_signature(submission(image))
    prepared.close()
    with pytest.raises(ValueError):
        prepared.image.getbbox()
