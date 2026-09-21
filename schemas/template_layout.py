from __future__ import annotations

from datetime import date, time
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Coordinate = Annotated[float, Field(ge=0.0, le=1.0)]
ElementKind = Literal[
    "text",
    "multiline",
    "date",
    "time",
    "date_range",
    "time_range",
    "qr",
    "signature",
]


class LayoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ElementBox(LayoutModel):
    x: Coordinate
    y: Coordinate
    width: Annotated[float, Field(gt=0.0, le=1.0)]
    height: Annotated[float, Field(gt=0.0, le=1.0)]

    @model_validator(mode="after")
    def stays_inside_canvas(self):
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("The element box must stay inside the canvas.")
        return self


class FontReference(LayoutModel):
    source: Literal["builtin", "uploaded"]
    key: Annotated[str | None, Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    id: Annotated[int | None, Field(default=None, gt=0)]

    @model_validator(mode="after")
    def matches_source(self):
        if self.source == "builtin" and (self.key is None or self.id is not None):
            raise ValueError("A builtin font requires only a key.")
        if self.source == "uploaded" and (self.id is None or self.key is not None):
            raise ValueError("An uploaded font requires only an id.")
        return self


class TextStyle(LayoutModel):
    font: FontReference
    font_size_px: Annotated[int, Field(ge=8, le=512)]
    color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
    weight: Annotated[int, Field(ge=100, le=900)] = 400
    align: Literal["left", "center", "right"] = "center"
    line_height: Annotated[float, Field(ge=1.0, le=3.0)] = 1.25

    @field_validator("weight")
    @classmethod
    def weight_uses_css_steps(cls, value: int) -> int:
        if value % 100:
            raise ValueError("Font weight must be a multiple of 100.")
        return value


class DateTimeFormat(LayoutModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    locale: Literal["th-TH"] = "th-TH"
    calendar: Literal["buddhist", "gregorian"] = "buddhist"
    pattern: Literal["short", "medium", "long", "numeric"] = "long"
    prefix: Annotated[str, Field(max_length=255)] = ""
    separator: Annotated[str, Field(max_length=255)] = " ถึง "
    suffix: Annotated[str, Field(max_length=255)] = ""


class TemplateElement(LayoutModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    source: Annotated[str, Field(min_length=1, max_length=71)]
    kind: ElementKind
    label: Annotated[str, Field(min_length=1, max_length=255)]
    required: bool = False
    visible: bool = True
    box: ElementBox
    style: TextStyle | None = None
    format: DateTimeFormat | None = None
    sample_value: Any | None = None
    show_id: bool | None = None

    @field_validator("source")
    @classmethod
    def source_is_supported(cls, value: str) -> str:
        standard = {
            "recipient_name",
            "topic",
            "issue_date",
            "verification_url",
            "verification_code",
            "signature",
        }
        if value in standard:
            return value
        if value.startswith("custom."):
            key = value.removeprefix("custom.")
            if key and len(key) <= 64 and key[0].isalpha() and key.isascii() and all(
                character.islower() or character.isdigit() or character == "_"
                for character in key
            ):
                return value
        raise ValueError("The template element source is invalid.")

    @model_validator(mode="after")
    def settings_match_kind(self):
        if self.kind != "qr" and self.show_id is not None:
            raise ValueError("Only QR elements support the built-in ID caption.")
        if self.source == "verification_code" and self.kind != "text":
            raise ValueError("The verification ID must be a text element.")
        if self.kind == "signature":
            if (
                self.source != "signature"
                or not self.required
                or self.style is None
                or self.style.align != "center"
                or self.format is not None
                or self.sample_value is not None
            ):
                raise ValueError("A signature element has invalid settings.")
            return self
        if self.source == "signature":
            raise ValueError("Only a signature element may use the signature source.")
        if self.kind == "qr":
            if self.source != "verification_url" or self.style is not None or self.format is not None:
                raise ValueError("A QR element has invalid settings.")
            self._validate_sample_value()
            return self
        if self.source == "verification_url" or self.style is None:
            raise ValueError("A text element requires text settings.")
        temporal_kinds = {"date", "time", "date_range", "time_range"}
        if self.kind in temporal_kinds and self.format is None:
            raise ValueError("A date or time element requires format settings.")
        if self.kind not in temporal_kinds and self.format is not None:
            raise ValueError("Only date or time elements support format settings.")
        self._validate_sample_value()
        return self

    def _validate_sample_value(self) -> None:
        value = self.sample_value
        if value is None:
            return
        range_kinds = {"date_range", "time_range"}
        if self.kind in range_kinds:
            if not isinstance(value, dict) or set(value) != {"start", "end"}:
                raise ValueError("A range sample requires start and end values.")
            samples = (value["start"], value["end"])
        else:
            if not isinstance(value, str):
                raise ValueError("A template sample value must be text.")
            samples = (value,)
        if any(
            not isinstance(sample, str) or len(sample) > 2000
            for sample in samples
        ):
            raise ValueError("Template sample values may contain at most 2,000 characters.")
        try:
            if self.kind in {"date", "date_range"}:
                for sample in samples:
                    date.fromisoformat(sample)
            elif self.kind in {"time", "time_range"}:
                for sample in samples:
                    time.fromisoformat(sample)
        except ValueError as exc:
            raise ValueError("Template temporal samples must use ISO format.") from exc


class TemplateLayout(LayoutModel):
    version: Literal[2]
    elements: Annotated[list[TemplateElement], Field(max_length=100)]

    @model_validator(mode="after")
    def element_identities_are_unique(self):
        ids = [element.id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("Template element ids must be unique.")
        custom_sources = [
            element.source
            for element in self.elements
            if element.source.startswith("custom.")
        ]
        if len(custom_sources) != len(set(custom_sources)):
            raise ValueError("Custom template sources must be unique.")
        serialized = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > 64 * 1024:
            raise ValueError("The template layout exceeds 64 KB.")
        return self


__all__ = [
    "DateTimeFormat",
    "ElementBox",
    "ElementKind",
    "FontReference",
    "TemplateElement",
    "TemplateLayout",
    "TextStyle",
]
