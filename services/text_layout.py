from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageDraw, ImageFont, features
from pythainlp.tokenize import word_tokenize
import regex

from config import settings
from schemas.template_layout import TextStyle


class TextOverflowError(ValueError):
    def __init__(self, element_id: str):
        self.element_id = element_id
        super().__init__(f'Text does not fit template element "{element_id}".')


class ThaiShapingUnavailableError(RuntimeError):
    """Raised when strict Thai shaping is enabled without a RAQM build."""


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def layout_engine_for_text(text: str) -> ImageFont.Layout:
    available = raqm_available()
    if regex.search(r"\p{Thai}", text) and settings.REQUIRE_RAQM and not available:
        raise ThaiShapingUnavailableError(
            "Thai text shaping requires Pillow with RAQM support."
        )
    return ImageFont.Layout.RAQM if available else ImageFont.Layout.BASIC


def validate_thai_shaping_configuration() -> None:
    if settings.REQUIRE_RAQM and not raqm_available():
        raise ThaiShapingUnavailableError(
            "REQUIRE_RAQM is enabled but Pillow has no RAQM support."
        )


@dataclass(frozen=True)
class TextLine:
    text: str
    position: tuple[float, float]
    anchor: str


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    options = (
        {"language": "th"}
        if font.layout_engine == ImageFont.Layout.RAQM
        else {}
    )
    return float(font.getlength(text, **options))


def _largest_fitting_prefix(
    graphemes: list[str],
    font: ImageFont.FreeTypeFont,
    maximum_width: int,
) -> int:
    low, high = 1, len(graphemes)
    result = 0
    while low <= high:
        middle = (low + high) // 2
        if _text_width(font, "".join(graphemes[:middle])) <= maximum_width:
            result = middle
            low = middle + 1
        else:
            high = middle - 1
    return result


def _wrap_paragraph(
    paragraph: str,
    font: ImageFont.FreeTypeFont,
    maximum_width: int,
    *,
    allow_overflow: bool = False,
) -> list[str]:
    remaining = paragraph.strip()
    if not remaining:
        return [""]
    lines: list[str] = []
    while remaining:
        if _text_width(font, remaining) <= maximum_width:
            lines.append(remaining.rstrip())
            break
        tokens = word_tokenize(
            remaining,
            engine="newmm",
            keep_whitespace=True,
        )
        fitting_token_count = 0
        for index in range(1, len(tokens) + 1):
            if _text_width(font, "".join(tokens[:index])) > maximum_width:
                break
            fitting_token_count = index
        if fitting_token_count:
            candidate = "".join(tokens[:fitting_token_count])
            line = candidate.rstrip()
            consumed = len(candidate)
        else:
            first_token = tokens[0] if tokens else remaining
            graphemes = regex.findall(r"\X", first_token)
            prefix_count = _largest_fitting_prefix(graphemes, font, maximum_width)
            if prefix_count <= 0:
                if not allow_overflow:
                    raise TextOverflowError("")
                # Always consume a complete grapheme, even in a one-pixel box.
                prefix_count = 1
            line = "".join(graphemes[:prefix_count]).rstrip()
            consumed = len("".join(graphemes[:prefix_count]))
        if not line:
            consumed = max(1, consumed)
        else:
            lines.append(line)
        remaining = remaining[consumed:].lstrip()
    return lines


def layout_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
    style: TextStyle,
    *,
    element_id: str = "text",
    allow_overflow: bool = False,
    wrap: bool = True,
) -> tuple[TextLine, ...]:
    del draw
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise TextOverflowError(element_id)

    lines: list[str] = []
    try:
        for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if wrap:
                lines.extend(_wrap_paragraph(paragraph, font, width, allow_overflow=allow_overflow))
            else:
                lines.append(paragraph)
    except TextOverflowError as exc:
        raise TextOverflowError(element_id) from exc

    glyph_box = font.getbbox("กี่Ay")
    glyph_height = max(1, glyph_box[3] - glyph_box[1])
    line_height = max(glyph_height, round(style.font_size_px * style.line_height))
    total_height = line_height * len(lines)
    if not lines or (total_height > height and not allow_overflow):
        raise TextOverflowError(element_id)

    vertical_offset = max(0, (height - total_height) / 2)
    if style.align == "left":
        x, anchor = float(left), "lt"
    elif style.align == "right":
        x, anchor = float(right), "rt"
    else:
        x, anchor = (left + right) / 2, "mt"
    return tuple(
        TextLine(
            text=line,
            position=(x, top + vertical_offset + index * line_height),
            anchor=anchor,
        )
        for index, line in enumerate(lines)
    )


__all__ = [
    "TextLine",
    "TextOverflowError",
    "ThaiShapingUnavailableError",
    "layout_engine_for_text",
    "layout_text",
    "raqm_available",
    "validate_thai_shaping_configuration",
]
