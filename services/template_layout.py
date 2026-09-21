from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from schemas.template_layout import TemplateLayout


class TemplateLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class CustomFieldSpec:
    key: str
    label: str
    kind: str
    required: bool


def normalize_layout(value: object) -> dict[str, Any]:
    try:
        model = TemplateLayout.model_validate(value)
    except ValidationError as exc:
        raise TemplateLayoutError("The template layout is invalid.") from exc
    return model.model_dump(mode="json", exclude_none=True)


def custom_field_specs(layout: object) -> tuple[CustomFieldSpec, ...]:
    normalized = normalize_layout(layout)
    return tuple(
        CustomFieldSpec(
            key=element["source"].removeprefix("custom."),
            label=element["label"],
            kind=element["kind"],
            required=element["required"],
        )
        for element in normalized["elements"]
        if element["visible"] and element["source"].startswith("custom.")
    )


def _dimensions(value: object) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise TemplateLayoutError("The template dimensions are invalid.")
    width = value.get("width")
    height = value.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise TemplateLayoutError("The template dimensions are invalid.")
    return width, height


def _legacy_position(value: object) -> tuple[float, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise TemplateLayoutError("The template positions are invalid.")
    x = value["x"]
    y = value["y"]
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not 0.0 <= x <= 1.0
        or not 0.0 <= y <= 1.0
    ):
        raise TemplateLayoutError("The template positions are invalid.")
    return float(x), float(y)


def _centered_box(
    center_x: float,
    center_y: float,
    width: float,
    height: float,
) -> dict[str, float]:
    width = min(1.0, max(0.001, width))
    height = min(1.0, max(0.001, height))
    return {
        "x": min(1.0 - width, max(0.0, center_x - width / 2)),
        "y": min(1.0 - height, max(0.0, center_y - height / 2)),
        "width": width,
        "height": height,
    }


def _legacy_text_element(
    element_id: str,
    source: str,
    label: str,
    kind: str,
    position: object,
    width: int,
    height: int,
    font_size: int,
) -> dict[str, Any]:
    center_x, center_y = _legacy_position(position)
    element: dict[str, Any] = {
        "id": element_id,
        "source": source,
        "kind": kind,
        "label": label,
        "required": True,
        "visible": True,
        "box": _centered_box(
            center_x,
            center_y,
            0.6,
            # Leave room for stacked Thai marks shaped by RAQM, including
            # rounding at small font sizes in legacy templates.
            max(0.04, font_size * 1.6 / height),
        ),
        "style": {
            "font": {"source": "builtin", "key": "sarabun"},
            "font_size_px": font_size,
            "color": "#000000",
            "weight": 400,
            "align": "center",
            "line_height": 1.25,
        },
    }
    if kind == "date":
        element["format"] = {
            "locale": "th-TH",
            "calendar": "gregorian",
            "pattern": "numeric",
            "prefix": "",
            "separator": " ถึง ",
            "suffix": "",
        }
    return element


def legacy_positions_to_layout(
    positions: object,
    dimensions: object,
) -> dict[str, Any]:
    if not isinstance(positions, dict):
        raise TemplateLayoutError("The template positions are invalid.")
    width, height = _dimensions(dimensions)
    minimum = min(width, height)
    text_size = max(14, round(minimum * 0.055))
    date_size = max(12, round(text_size * 0.72))
    elements: list[dict[str, Any]] = []

    text_definitions = (
        ("recipient_name", ("recipient_name", "name"), "recipient_name", "ชื่อผู้รับ", "text", text_size),
        ("topic", ("topic",), "topic", "หัวข้อ / กิจกรรม", "text", text_size),
        ("issue_date", ("issue_date",), "issue_date", "วันที่ออก", "date", date_size),
    )
    for element_id, aliases, source, label, kind, font_size in text_definitions:
        legacy_key = next((key for key in aliases if key in positions), None)
        if legacy_key is None:
            continue
        elements.append(
            _legacy_text_element(
                element_id,
                source,
                label,
                kind,
                positions[legacy_key],
                width,
                height,
                font_size,
            )
        )

    qr_key = next((key for key in ("qr_code", "qr") if key in positions), None)
    if qr_key is not None:
        center_x, center_y = _legacy_position(positions[qr_key])
        qr_size = max(48, round(minimum * 0.24))
        elements.append({
            "id": "verification_qr",
            "source": "verification_url",
            "kind": "qr",
            "label": "คิวอาร์โค้ด",
            "required": True,
            "visible": True,
            "box": _centered_box(
                center_x,
                center_y,
                qr_size / width,
                qr_size / height,
            ),
        })

    return normalize_layout({"version": 2, "elements": elements})


__all__ = [
    "CustomFieldSpec",
    "TemplateLayoutError",
    "custom_field_specs",
    "legacy_positions_to_layout",
    "normalize_layout",
]
