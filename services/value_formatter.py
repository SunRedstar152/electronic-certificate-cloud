from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from schemas.template_layout import DateTimeFormat, TemplateElement


THAI_MONTHS = (
    "",
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
)
THAI_MONTHS_SHORT = (
    "",
    "ม.ค.",
    "ก.พ.",
    "มี.ค.",
    "เม.ย.",
    "พ.ค.",
    "มิ.ย.",
    "ก.ค.",
    "ส.ค.",
    "ก.ย.",
    "ต.ค.",
    "พ.ย.",
    "ธ.ค.",
)


class ValueFormatError(ValueError):
    """Raised when a stored template value cannot be formatted safely."""


def _value_for_source(element: TemplateElement, values: dict[str, Any]) -> Any:
    if element.source in values:
        return values[element.source]
    if element.source.startswith("custom."):
        key = element.source.removeprefix("custom.")
        custom_values = values.get("custom_fields", values.get("custom"))
        if isinstance(custom_values, dict):
            return custom_values.get(key)
    return None


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueFormatError("The date value must use ISO format.") from exc
    raise ValueFormatError("The date value is invalid.")


def _as_time(value: Any) -> time:
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return time.fromisoformat(value).replace(tzinfo=None)
        except ValueError as exc:
            raise ValueFormatError("The time value must use ISO format.") from exc
    raise ValueFormatError("The time value is invalid.")


def _display_year(value: date, settings: DateTimeFormat) -> int:
    return value.year + 543 if settings.calendar == "buddhist" else value.year


def _format_date(value: date, settings: DateTimeFormat) -> str:
    year = _display_year(value, settings)
    if settings.pattern == "numeric":
        return f"{year:04d}-{value.month:02d}-{value.day:02d}"
    if settings.pattern == "short":
        return f"{value.day}/{value.month}/{year}"
    if settings.pattern == "medium":
        return f"{value.day} {THAI_MONTHS_SHORT[value.month]} {year}"
    return f"{value.day} {THAI_MONTHS[value.month]} {year}"


def _format_time(value: time, settings: DateTimeFormat) -> str:
    if settings.pattern == "long" and (value.second or value.microsecond):
        return value.strftime("%H:%M:%S")
    return value.strftime("%H:%M")


def _range(value: Any) -> tuple[Any, Any]:
    if isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
    else:
        raise ValueFormatError("A range value requires start and end values.")
    if start in (None, "") or end in (None, ""):
        raise ValueFormatError("A range value requires start and end values.")
    return start, end


def format_element_value(
    element: TemplateElement | dict[str, Any],
    values: dict[str, Any],
) -> str | None:
    if not isinstance(element, TemplateElement):
        element = TemplateElement.model_validate(element)
    value = _value_for_source(element, values)
    if element.source == "verification_code":
        if not value:
            try:
                value = parse_qs(urlsplit(str(values.get("verification_url", ""))).query).get("code", [""])[0]
            except ValueError:
                value = ""
        return f"ID: {value}" if value else None
    if value is None or value == "":
        return None
    if element.kind == "qr":
        return str(value)
    if element.kind in {"text", "multiline"}:
        return str(value)

    settings = element.format
    if settings is None:
        raise ValueFormatError("A date or time element requires format settings.")

    if element.kind == "date":
        content = _format_date(_as_date(value), settings)
    elif element.kind == "time":
        content = _format_time(_as_time(value), settings)
    elif element.kind == "date_range":
        raw_start, raw_end = _range(value)
        start, end = _as_date(raw_start), _as_date(raw_end)
        if start > end:
            raise ValueFormatError("The range start must not be after its end.")
        content = (
            f"{_format_date(start, settings)}"
            f"{settings.separator}"
            f"{_format_date(end, settings)}"
        )
    elif element.kind == "time_range":
        raw_start, raw_end = _range(value)
        start, end = _as_time(raw_start), _as_time(raw_end)
        if start > end:
            raise ValueFormatError("The range start must not be after its end.")
        content = (
            f"{_format_time(start, settings)}"
            f"{settings.separator}"
            f"{_format_time(end, settings)}"
        )
    else:  # pragma: no cover - protected by the schema's literal values
        raise ValueFormatError("The template element kind is unsupported.")
    return f"{settings.prefix}{content}{settings.suffix}"


__all__ = [
    "THAI_MONTHS",
    "THAI_MONTHS_SHORT",
    "ValueFormatError",
    "format_element_value",
]
