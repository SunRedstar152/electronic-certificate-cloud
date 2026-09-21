import pytest

from services.template_layout import (
    TemplateLayoutError,
    custom_field_specs,
    legacy_positions_to_layout,
    normalize_layout,
)


def text_element(**changes):
    value = {
        "id": "recipient_name",
        "source": "recipient_name",
        "kind": "text",
        "label": "ชื่อผู้รับ",
        "required": True,
        "visible": True,
        "box": {"x": 0.2, "y": 0.4, "width": 0.6, "height": 0.1},
        "style": {
            "font": {"source": "builtin", "key": "sarabun"},
            "font_size_px": 52,
            "color": "#111111",
            "weight": 700,
            "align": "center",
            "line_height": 1.25,
        },
    }
    value.update(changes)
    return value


def signature_element(**changes):
    value = {
        "id": "signature",
        "source": "signature",
        "kind": "signature",
        "label": "ลายเซ็นผู้ลงนาม",
        "required": True,
        "visible": True,
        "box": {"x": 0.35, "y": 0.66, "width": 0.30, "height": 0.24},
        "style": {
            "font": {"source": "builtin", "key": "sarabun"},
            "font_size_px": 28,
            "color": "#111111",
            "weight": 400,
            "align": "center",
            "line_height": 1.2,
        },
    }
    value.update(changes)
    return value


def test_normalize_layout_preserves_a_valid_text_element():
    layout = normalize_layout({
        "version": 2,
        "elements": [text_element()],
    })

    assert layout["version"] == 2
    assert layout["elements"][0]["label"] == "ชื่อผู้รับ"
    assert layout["elements"][0]["style"]["font_size_px"] == 52


def test_normalize_layout_rejects_duplicate_element_ids():
    with pytest.raises(TemplateLayoutError):
        normalize_layout({
            "version": 2,
            "elements": [
                text_element(),
                text_element(source="topic", label="หัวข้อ"),
            ],
        })


def test_normalize_layout_rejects_a_box_outside_the_canvas():
    with pytest.raises(TemplateLayoutError):
        normalize_layout({
            "version": 2,
            "elements": [
                text_element(box={"x": 0.8, "y": 0.4, "width": 0.4, "height": 0.1})
            ],
        })


def test_custom_field_specs_are_unique_and_follow_layout_order():
    layout = normalize_layout({
        "version": 2,
        "elements": [
            text_element(
                id="department",
                source="custom.department",
                label="หน่วยงาน",
                required=False,
            ),
            text_element(
                id="location",
                source="custom.location",
                label="สถานที่",
                required=True,
            ),
        ],
    })

    assert [spec.key for spec in custom_field_specs(layout)] == [
        "department",
        "location",
    ]
    assert custom_field_specs(layout)[1].required is True


def test_normalize_layout_rejects_duplicate_custom_sources():
    with pytest.raises(TemplateLayoutError):
        normalize_layout({
            "version": 2,
            "elements": [
                text_element(id="department", source="custom.department"),
                text_element(id="department_again", source="custom.department"),
            ],
        })


def test_normalize_layout_accepts_one_required_centered_signature_block():
    layout = normalize_layout({"version": 2, "elements": [signature_element()]})

    assert layout["elements"][0]["source"] == "signature"
    assert layout["elements"][0]["kind"] == "signature"


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "custom.signature"},
        {"required": False},
        {"format": {"locale": "th-TH", "calendar": "buddhist", "pattern": "long"}},
        {"sample_value": "not allowed"},
        {"style": {**signature_element()["style"], "align": "left"}},
    ],
)
def test_normalize_layout_rejects_invalid_signature_settings(changes):
    with pytest.raises(TemplateLayoutError):
        normalize_layout({"version": 2, "elements": [signature_element(**changes)]})


def test_normalize_layout_accepts_multiple_signature_blocks_with_unique_ids():
    layout = normalize_layout({
        "version": 2,
        "elements": [signature_element(id=f"signature_{index}") for index in range(3)],
    })
    assert [element["id"] for element in layout["elements"]] == [
        "signature_0", "signature_1", "signature_2",
    ]
    assert custom_field_specs(layout) == ()


@pytest.mark.parametrize(
    "sample_value",
    ["x" * 2001, {"start": "2026-08-13"}, ["2026-08-13", "2026-08-15"]],
)
def test_normalize_layout_rejects_invalid_sample_shape_or_length(sample_value):
    with pytest.raises(TemplateLayoutError):
        normalize_layout({
            "version": 2,
            "elements": [text_element(sample_value=sample_value)],
        })


def test_normalize_layout_rejects_oversized_serialized_payload():
    elements = [
        text_element(
            id=f"field_{index}",
            source=f"custom.field_{index}",
            sample_value="ก" * 1000,
        )
        for index in range(30)
    ]

    with pytest.raises(TemplateLayoutError):
        normalize_layout({"version": 2, "elements": elements})


def test_legacy_qr_code_position_becomes_verification_qr():
    layout = legacy_positions_to_layout(
        {"qr_code": {"x": 0.85, "y": 0.82}},
        {"width": 1600, "height": 1131},
    )

    assert layout["elements"][0]["id"] == "verification_qr"
    assert layout["elements"][0]["kind"] == "qr"
    assert layout["elements"][0]["source"] == "verification_url"


def test_legacy_name_alias_becomes_recipient_name_with_original_font_formula():
    layout = legacy_positions_to_layout(
        {"name": {"x": 0.5, "y": 0.43}},
        {"width": 1600, "height": 1131},
    )

    element = layout["elements"][0]
    assert element["id"] == "recipient_name"
    assert element["source"] == "recipient_name"
    assert element["style"]["font_size_px"] == 62
    assert element["box"]["x"] < 0.5 < element["box"]["x"] + element["box"]["width"]
