"""Safe, bounded parsing for bulk certificate recipient imports.

This module deliberately treats every imported cell as text.  It does not
evaluate spreadsheet formulas or render HTML; callers receive normalized row
objects that can be previewed and persisted by the batch service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
import posixpath
import re
from typing import Any, Iterable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError, iterparse

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from services.template_layout import CustomFieldSpec


MAX_IMPORT_ROWS = 5_000
MAX_IMPORT_COLUMNS = 30
MAX_RECIPIENT_VALUE_LENGTH = 255
MAX_CELL_LENGTH = 4_096
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_XLSX_ARCHIVE_ENTRIES = 100
MAX_XLSX_SHARED_STRINGS = MAX_IMPORT_ROWS * MAX_IMPORT_COLUMNS
MAX_XLSX_STYLE_RECORDS = 2_000
MAX_XLSX_STYLE_OBJECTS_PER_TYPE = 2_000
MAX_XLSX_STYLE_OBJECTS_TOTAL = 10_000
MAX_XLSX_NUMBER_FORMAT_LENGTH = 255
MAX_XLSX_XML_ELEMENTS = 500_000
MAX_XLSX_METADATA_BYTES = 1 * 1024 * 1024
MAX_XLSX_METADATA_OBJECTS = 2_000

IMPORT_REQUIRED_VALUE = "IMPORT_REQUIRED_VALUE"
IMPORT_DUPLICATE_REFERENCE = "IMPORT_DUPLICATE_REFERENCE"
IMPORT_VALUE_TOO_LONG = "IMPORT_VALUE_TOO_LONG"


class ImportParseError(ValueError):
    """A stable, safe error for an invalid import source or column mapping."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ParsedRow:
    """One source row, retaining its original one-based file row number."""

    row_number: int
    values: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", dict(self.values))


@dataclass(frozen=True)
class ParsedTable:
    """Normalized source headers and inert textual cell values."""

    headers: tuple[str, ...]
    rows: tuple[ParsedRow, ...]


@dataclass(frozen=True)
class ImportRowError:
    """A deterministic validation error that Task 8 can persist per row."""

    code: str
    message: str
    field: str


@dataclass(frozen=True)
class NormalizedRecipient:
    """A mapped recipient row ready for batch preview or persistence."""

    row_number: int
    recipient_reference: str | None
    recipient_name: str | None
    custom_fields: dict[str, Any]
    errors: tuple[ImportRowError, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "custom_fields", dict(self.custom_fields))

    @property
    def valid(self) -> bool:
        return not self.errors


def parse_xlsx(data: bytes) -> ParsedTable:
    """Parse the active XLSX sheet in read-only/data-only mode.

    ``data_only=True`` returns an existing cached formula result if present and
    never evaluates formulas.  Cells without a cached result remain empty.
    """

    _validate_source_size(data, "XLSX")
    try:
        with ZipFile(BytesIO(data)) as archive:
            _preflight_xlsx_archive(archive)
    except BadZipFile as error:
        raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX file is invalid.") from error

    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError) as error:
        raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX file is invalid.") from error

    try:
        worksheet = workbook.active
        row_iterator = worksheet.iter_rows()
        header_cells = next(row_iterator, None)
        if header_cells is None:
            raise ImportParseError("IMPORT_HEADER_REQUIRED", "The import must include a header row.")

        headers = _validate_headers([_display_cell(cell) for cell in header_cells])
        parsed_rows: list[ParsedRow] = []
        for source_row_number, cells in enumerate(row_iterator, start=2):
            if len(parsed_rows) >= MAX_IMPORT_ROWS:
                raise ImportParseError(
                    "IMPORT_ROW_LIMIT_EXCEEDED",
                    f"An import can contain at most {MAX_IMPORT_ROWS} data rows.",
                )
            values = [_display_cell(cell) for cell in cells]
            parsed_rows.append(_make_parsed_row(headers, source_row_number, values))

        return ParsedTable(headers=headers, rows=tuple(parsed_rows))
    finally:
        workbook.close()


def map_rows(
    table: ParsedTable,
    mapping: Mapping[str, str],
    *,
    field_specs: Iterable[CustomFieldSpec] | None = None,
    constants: Mapping[str, str] | None = None,
) -> list[NormalizedRecipient]:
    """Map source columns to recipient fields and attach stable row errors.

    The mapping uses target-field to source-header pairs.  The required target
    fields are ``recipient_reference`` and ``recipient_name``; any additional
    targets become ``custom_fields`` on the normalized recipient.
    """

    specs = None if field_specs is None else tuple(field_specs)
    normalized_mapping = _validate_mapping(table, mapping, specs)
    constants_dict = constants or {}
    recipients: list[NormalizedRecipient] = []
    seen_references: set[str] = set()

    for row in table.rows:
        mapped = {
            target: row.values[source]
            for target, source in normalized_mapping.items()
        }
        for target, value in constants_dict.items():
            mapped[target] = value

        reference = _normalize_value(mapped.get("recipient_reference", ""))
        name = _normalize_value(mapped.get("recipient_name", ""))
        if specs is None:
            custom_fields: dict[str, Any] = {
                target: _normalize_value(value)
                for target, value in mapped.items()
                if target not in {"recipient_reference", "recipient_name"}
            }
        else:
            custom_fields = {}
            for spec in specs:
                target = f"custom.{spec.key}"
                if spec.kind in {"date_range", "time_range"}:
                    start_target, end_target = f"{target}.start", f"{target}.end"
                    if start_target in mapped or end_target in mapped:
                        custom_fields[spec.key] = {
                            "start": _normalize_value(mapped.get(start_target, "")),
                            "end": _normalize_value(mapped.get(end_target, "")),
                        }
                elif target in mapped:
                    custom_fields[spec.key] = _normalize_value(mapped[target])
        errors = _validate_recipient_values(reference, name, custom_fields)

        if reference and len(reference) <= MAX_RECIPIENT_VALUE_LENGTH:
            if reference in seen_references:
                errors.append(
                    ImportRowError(
                        code=IMPORT_DUPLICATE_REFERENCE,
                        message="The recipient reference is duplicated in this import.",
                        field="recipient_reference",
                    )
                )
            else:
                seen_references.add(reference)

        recipients.append(
            NormalizedRecipient(
                row_number=row.row_number,
                recipient_reference=reference or None,
                recipient_name=name or None,
                custom_fields=custom_fields,
                errors=tuple(errors),
            )
        )

    return recipients


def _validate_headers(header_values: Sequence[str]) -> tuple[str, ...]:
    if not header_values:
        raise ImportParseError("IMPORT_HEADER_REQUIRED", "The import must include a header row.")
    if len(header_values) > MAX_IMPORT_COLUMNS:
        raise ImportParseError(
            "IMPORT_COLUMN_LIMIT_EXCEEDED",
            f"An import can contain at most {MAX_IMPORT_COLUMNS} columns.",
        )
    if any(len(str(value)) > MAX_CELL_LENGTH for value in header_values):
        raise ImportParseError(
            IMPORT_VALUE_TOO_LONG,
            f"Import cell values must not exceed {MAX_CELL_LENGTH} characters.",
        )

    headers = tuple(_normalize_header(value) for value in header_values)
    if any(not header for header in headers):
        raise ImportParseError("IMPORT_INVALID_HEADER", "Every import column must have a header.")
    if len(set(headers)) != len(headers):
        raise ImportParseError(
            "IMPORT_DUPLICATE_HEADER", "Import column headers must be unique after normalization."
        )
    return headers


def _make_parsed_row(
    headers: tuple[str, ...], source_row_number: int, values: Sequence[str]
) -> ParsedRow:
    if len(values) > len(headers):
        raise ImportParseError(
            "IMPORT_ROW_TOO_WIDE", f"Row {source_row_number} has more cells than the header row."
        )
    text_values = tuple(str(value) for value in values)
    if any(len(value) > MAX_CELL_LENGTH for value in text_values):
        raise ImportParseError(
            IMPORT_VALUE_TOO_LONG,
            f"Import cell values must not exceed {MAX_CELL_LENGTH} characters.",
        )
    padded_values = text_values + ("",) * (len(headers) - len(values))
    return ParsedRow(
        row_number=source_row_number,
        values=dict(zip(headers, padded_values, strict=True)),
    )


def _validate_mapping(
    table: ParsedTable,
    mapping: Mapping[str, str],
    field_specs: tuple[CustomFieldSpec, ...] | None = None,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for target, source in mapping.items():
        target_name = str(target).strip()
        source_name = _normalize_header(str(source))
        if not target_name:
            raise ImportParseError("IMPORT_INVALID_MAPPING", "Mapped field names cannot be empty.")
        if target_name in normalized:
            raise ImportParseError(
                "IMPORT_INVALID_MAPPING", "Mapped field names must be unique after normalization."
            )
        if source_name not in table.headers:
            raise ImportParseError(
                "IMPORT_MAPPING_COLUMN_NOT_FOUND",
                f"The mapped source column '{source_name}' does not exist in the import.",
            )
        normalized[target_name] = source_name

    missing_fields = {"recipient_reference", "recipient_name"} - normalized.keys()
    if missing_fields:
        raise ImportParseError(
            "IMPORT_MAPPING_REQUIRED_FIELD",
            "The mapping must include recipient_reference and recipient_name.",
        )
    if field_specs is not None:
        allowed = {"recipient_reference", "recipient_name"}
        required: set[str] = set()
        for spec in field_specs:
            target = f"custom.{spec.key}"
            if spec.kind in {"date_range", "time_range"}:
                targets = {f"{target}.start", f"{target}.end"}
            else:
                targets = {target}
            allowed.update(targets)
            if spec.required:
                required.update(targets)
        if not set(normalized).issubset(allowed):
            raise ImportParseError(
                "IMPORT_INVALID_MAPPING",
                "The mapping contains a field not defined by the template.",
            )
        missing_template_fields = required - set(normalized)
        if missing_template_fields:
            raise ImportParseError(
                "IMPORT_MAPPING_REQUIRED_FIELD",
                "The mapping is missing a required template field.",
            )
        for spec in field_specs:
            if spec.kind not in {"date_range", "time_range"}:
                continue
            target = f"custom.{spec.key}"
            endpoints = {f"{target}.start", f"{target}.end"}
            if endpoints.intersection(normalized) and not endpoints.issubset(normalized):
                raise ImportParseError(
                    "IMPORT_INVALID_MAPPING",
                    "A range mapping requires both start and end columns.",
                )
    return normalized


def _validate_recipient_values(
    reference: str, name: str, custom_fields: Mapping[str, Any]
) -> list[ImportRowError]:
    errors: list[ImportRowError] = []
    for field, value in (("recipient_reference", reference), ("recipient_name", name), *custom_fields.items()):
        if field in {"recipient_reference", "recipient_name"} and not value:
            errors.append(
                ImportRowError(
                    code=IMPORT_REQUIRED_VALUE,
                    message=f"The {field} value is required.",
                    field=field,
                )
            )
        elif isinstance(value, str) and len(value) > MAX_RECIPIENT_VALUE_LENGTH:
            errors.append(
                ImportRowError(
                    code=IMPORT_VALUE_TOO_LONG,
                    message=(
                        f"The {field} value must not exceed "
                        f"{MAX_RECIPIENT_VALUE_LENGTH} characters."
                    ),
                    field=field,
                )
            )
        elif isinstance(value, dict) and any(
            not isinstance(item, str) or len(item) > MAX_RECIPIENT_VALUE_LENGTH
            for item in value.values()
        ):
            errors.append(
                ImportRowError(
                    code=IMPORT_VALUE_TOO_LONG,
                    message=(
                        f"The {field} range values must not exceed "
                        f"{MAX_RECIPIENT_VALUE_LENGTH} characters."
                    ),
                    field=field,
                )
            )
    return errors


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", str(value).strip().casefold()).strip("_")


def _normalize_value(value: str) -> str:
    return value.strip()


def _display_cell(cell: object) -> str:
    value = getattr(cell, "value", cell)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    number_format = str(getattr(cell, "number_format", ""))
    if isinstance(value, (datetime, date, time)):
        return _format_date_display(value, number_format)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_number_display(value, number_format)
    return str(value)


def _validate_source_size(data: bytes, source_name: str) -> None:
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportParseError(
            "IMPORT_FILE_TOO_LARGE", f"The {source_name} file exceeds the maximum allowed size."
        )


def _format_date_display(value: datetime | date | time, number_format: str) -> str:
    """Render the common Excel date patterns used in recipient import sheets."""

    format_string = number_format.split(";", maxsplit=1)[0]
    format_string = re.sub(r"\[[^]]*\]", "", format_string).replace("\\", "")
    if not format_string or format_string.casefold() == "general":
        return value.isoformat()

    return _render_excel_datetime_format(value, format_string)


def _format_number_display(value: int | float, number_format: str) -> str:
    """Keep common Excel numeric display formats, including leading zero masks."""

    format_string = _clean_number_format(number_format)
    if not format_string or format_string.casefold() == "general":
        return str(value)

    match = re.fullmatch(
        r"(?P<prefix>[^0#]*)(?P<pattern>[0#,-]+(?:\.[0#]+)?)(?P<suffix>[% ]*)",
        format_string,
    )
    if not match:
        return str(value)

    pattern = match.group("pattern")
    multiplier = 100 if "%" in match.group("suffix") else 1
    integer_mask, _, decimal_mask = pattern.partition(".")
    decimals = len(decimal_mask)
    absolute = abs(value * multiplier)
    grouping = "," in integer_mask
    rounded = f"{absolute:{',' if grouping else ''}.{decimals}f}"
    integer_digits, _, decimal_digits = rounded.partition(".")
    if grouping:
        plain_digits = integer_digits.replace(",", "").zfill(integer_mask.count("0"))
        groups: list[str] = []
        while plain_digits:
            groups.append(plain_digits[-3:])
            plain_digits = plain_digits[:-3]
        rendered_integer = ",".join(reversed(groups))
    else:
        mandatory_integer_digits = integer_mask.count("0")
        integer_digits = integer_digits.zfill(mandatory_integer_digits)
        rendered_integer = _apply_integer_mask(integer_digits, integer_mask)
    sign = "-" if value < 0 else ""
    decimal = f".{decimal_digits}" if decimal_mask else ""
    return (
        f"{match.group('prefix')}{sign}{rendered_integer}{decimal}"
        f"{match.group('suffix')}"
    )


def _clean_number_format(number_format: str) -> str:
    primary_section = number_format.split(";", maxsplit=1)[0]
    without_brackets = re.sub(r"\[[^]]*\]", "", primary_section)
    return without_brackets.replace('"', "").replace("\\", "")


def _preflight_xlsx_archive(archive: ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_XLSX_ARCHIVE_ENTRIES:
        raise ImportParseError(
            "IMPORT_FILE_TOO_COMPLEX", "The XLSX file contains too many archive entries."
        )
    if sum(member.file_size for member in members) > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise ImportParseError(
            "IMPORT_FILE_TOO_LARGE", "The XLSX file expands beyond the maximum allowed size."
        )

    discovered_parts = _discover_xlsx_parts(archive)
    total_style_objects = 0
    for member in members:
        if member.file_size > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise ImportParseError(
                "IMPORT_FILE_TOO_LARGE", "An XLSX archive entry exceeds the maximum allowed size."
            )
        normalized_name = _normalize_zip_part(member.filename)
        kind = discovered_parts.get(normalized_name)
        if kind == "sharedStrings" or normalized_name == "xl/sharedStrings.xml":
            _count_xlsx_xml_objects(
                archive, member, "si", MAX_XLSX_SHARED_STRINGS
            )
        elif kind == "styles" or normalized_name == "xl/styles.xml":
            total_style_objects += _count_xlsx_style_objects(archive, member)
            if total_style_objects > MAX_XLSX_STYLE_OBJECTS_TOTAL:
                raise ImportParseError(
                    "IMPORT_FILE_TOO_COMPLEX", "The XLSX file contains too many style objects."
                )
        elif kind == "worksheet" or (
            normalized_name.startswith("xl/worksheets/") and normalized_name.endswith(".xml")
        ):
            _count_xlsx_xml_objects(
                archive,
                member,
                "c",
                (MAX_IMPORT_ROWS + 1) * MAX_IMPORT_COLUMNS,
            )


def _count_xlsx_xml_objects(
    archive: ZipFile, member: object, local_name: str, object_limit: int
) -> int:
    object_count = 0
    element_count = 0
    try:
        with archive.open(member) as source:
            for _, element in iterparse(source, events=("end",)):
                element_count += 1
                if element.tag.rsplit("}", maxsplit=1)[-1] == local_name:
                    object_count += 1
                    if object_count > object_limit:
                        raise ImportParseError(
                            "IMPORT_FILE_TOO_COMPLEX",
                            "The XLSX file contains too many spreadsheet objects.",
                        )
                if element_count > MAX_XLSX_XML_ELEMENTS:
                    raise ImportParseError(
                        "IMPORT_FILE_TOO_COMPLEX",
                        "The XLSX file contains overly complex XML.",
                    )
                element.clear()
    except ParseError as error:
        raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX file contains invalid XML.") from error
    return object_count


def _discover_xlsx_parts(archive: ZipFile) -> dict[str, str]:
    parts: dict[str, str] = {}
    names = {_normalize_zip_part(name) for name in archive.namelist()}
    if "[Content_Types].xml" in names:
        _validate_xlsx_metadata_member(archive, "[Content_Types].xml")
        override_count = 0
        element_count = 0
        try:
            with archive.open("[Content_Types].xml") as source:
                for _, element in iterparse(source, events=("end",)):
                    element_count += 1
                    if element_count > MAX_XLSX_METADATA_OBJECTS:
                        raise ImportParseError(
                            "IMPORT_FILE_TOO_COMPLEX", "The XLSX content types are too complex."
                        )
                    if element.tag.rsplit("}", 1)[-1] == "Override":
                        override_count += 1
                        if override_count > MAX_XLSX_METADATA_OBJECTS:
                            raise ImportParseError(
                                "IMPORT_FILE_TOO_COMPLEX",
                                "The XLSX file contains too many content type overrides.",
                            )
                        part = _normalize_zip_part(element.attrib.get("PartName", ""))
                        kind = _ooxml_kind(element.attrib.get("ContentType", ""))
                        if part and kind:
                            parts[part] = kind
                    element.clear()
        except ParseError as error:
            raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX content types are invalid.") from error

    for relationship_name in names:
        if not relationship_name.endswith(".rels"):
            continue
        _validate_xlsx_metadata_member(archive, relationship_name)
        source_part = _relationship_source_part(relationship_name)
        relationship_count = 0
        element_count = 0
        try:
            with archive.open(relationship_name) as source:
                for _, element in iterparse(source, events=("end",)):
                    element_count += 1
                    if element_count > MAX_XLSX_METADATA_OBJECTS:
                        raise ImportParseError(
                            "IMPORT_FILE_TOO_COMPLEX", "The XLSX relationships are too complex."
                        )
                    if element.tag.rsplit("}", 1)[-1] == "Relationship":
                        relationship_count += 1
                        if relationship_count > MAX_XLSX_METADATA_OBJECTS:
                            raise ImportParseError(
                                "IMPORT_FILE_TOO_COMPLEX",
                                "The XLSX file contains too many relationships.",
                            )
                        kind = _ooxml_kind(element.attrib.get("Type", ""))
                        if kind and element.attrib.get("TargetMode") != "External":
                            target = _resolve_relationship_target(
                                source_part, element.attrib.get("Target", "")
                            )
                            if target:
                                parts[target] = kind
                    element.clear()
        except ParseError as error:
            raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX relationships are invalid.") from error
    return parts


def _validate_xlsx_metadata_member(archive: ZipFile, member_name: str) -> None:
    try:
        member = archive.getinfo(member_name)
    except KeyError as error:
        raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX metadata is missing.") from error
    if member.file_size > MAX_XLSX_METADATA_BYTES:
        raise ImportParseError(
            "IMPORT_FILE_TOO_COMPLEX", "An XLSX metadata part exceeds the safe size limit."
        )


def _normalize_zip_part(name: str) -> str:
    normalized = posixpath.normpath(name.replace("\\", "/").lstrip("/"))
    if normalized in {"", "."} or normalized.startswith("../"):
        return ""
    return normalized


def _relationship_source_part(relationship_name: str) -> str:
    directory, filename = posixpath.split(relationship_name)
    if posixpath.basename(directory) != "_rels" or not filename.endswith(".rels"):
        return ""
    return posixpath.join(posixpath.dirname(directory), filename[:-5])


def _resolve_relationship_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return _normalize_zip_part(target)
    return _normalize_zip_part(posixpath.join(posixpath.dirname(source_part), target))


def _ooxml_kind(value: str) -> str | None:
    lowered = value.casefold()
    for marker, kind in (
        ("sharedstrings", "sharedStrings"),
        ("styles", "styles"),
        ("worksheet", "worksheet"),
    ):
        if marker in lowered:
            return kind
    return None


def _count_xlsx_style_objects(archive: ZipFile, member: object) -> int:
    style_tags = {"font", "fill", "border", "numFmt", "xf", "cellStyle", "dxf"}
    counts = {tag: 0 for tag in style_tags}
    element_count = 0
    try:
        with archive.open(member) as source:
            for _, element in iterparse(source, events=("end",)):
                element_count += 1
                tag = element.tag.rsplit("}", 1)[-1]
                if tag in counts:
                    counts[tag] += 1
                    limit = MAX_XLSX_STYLE_RECORDS if tag == "xf" else MAX_XLSX_STYLE_OBJECTS_PER_TYPE
                    if counts[tag] > limit:
                        raise ImportParseError(
                            "IMPORT_FILE_TOO_COMPLEX", "The XLSX file contains too many style objects."
                        )
                    if (
                        tag == "numFmt"
                        and len(element.attrib.get("formatCode", ""))
                        > MAX_XLSX_NUMBER_FORMAT_LENGTH
                    ):
                        raise ImportParseError(
                            "IMPORT_FILE_TOO_COMPLEX",
                            "An XLSX number format exceeds the safe length limit.",
                        )
                if element_count > MAX_XLSX_XML_ELEMENTS:
                    raise ImportParseError(
                        "IMPORT_FILE_TOO_COMPLEX", "The XLSX file contains overly complex XML."
                    )
                element.clear()
    except ParseError as error:
        raise ImportParseError("IMPORT_INVALID_XLSX", "The XLSX styles are invalid.") from error
    return sum(counts.values())


def _render_excel_date_part(
    value: datetime | date | time, format_part: str, *, is_time_part: bool
) -> str:
    if is_time_part:
        hour = value.hour if isinstance(value, (datetime, time)) else 0
        minute = value.minute if isinstance(value, (datetime, time)) else 0
        second = value.second if isinstance(value, (datetime, time)) else 0
        replacements = {
            "hh": f"{hour:02d}",
            "h": str(hour),
            "mm": f"{minute:02d}",
            "m": str(minute),
            "ss": f"{second:02d}",
            "s": str(second),
        }
    elif isinstance(value, time):
        return format_part
    else:
        replacements = {
            "yyyy": f"{value.year:04d}",
            "yy": f"{value.year % 100:02d}",
            "mmmm": value.strftime("%B"),
            "mmm": value.strftime("%b"),
            "mm": f"{value.month:02d}",
            "m": str(value.month),
            "dddd": value.strftime("%A"),
            "ddd": value.strftime("%a"),
            "dd": f"{value.day:02d}",
            "d": str(value.day),
        }
    token_pattern = "|".join(sorted(replacements, key=len, reverse=True))
    return re.sub(
        token_pattern,
        lambda match: replacements[match.group(0).casefold()],
        format_part,
        flags=re.I,
    )


def _render_excel_datetime_format(value: datetime | date | time, format_string: str) -> str:
    """Render supported Excel date/time tokens while preserving quoted literals."""

    token_pattern = re.compile(
        r'"[^"]*"|AM/PM|A/P|yyyy|mmmm|dddd|mmm|ddd|yy|hh|ss|dd|mm|h|s|d|m',
        re.I,
    )
    tokens = list(token_pattern.finditer(format_string))
    twelve_hour = any(
        item.group(0).casefold() in {"am/pm", "a/p"} for item in tokens
    )
    rendered: list[str] = []
    position = 0
    time_mode = isinstance(value, time)
    for index, match in enumerate(tokens):
        rendered.append(format_string[position : match.start()])
        raw_token = match.group(0)
        token = raw_token.casefold()
        if raw_token.startswith('"'):
            rendered.append(raw_token[1:-1])
            position = match.end()
            continue
        between_previous = format_string[tokens[index - 1].end() : match.start()] if index else ""
        next_separator = (
            format_string[match.end() : tokens[index + 1].start()]
            if index + 1 < len(tokens)
            else ""
        )
        if token in {"h", "hh", "s", "ss", "am/pm", "a/p"}:
            time_mode = True
        minute_token = token in {"m", "mm"} and (
            time_mode or ":" in between_previous or ":" in next_separator
        )
        rendered.append(
            _excel_datetime_token(
                value,
                token,
                minute_token=minute_token,
                twelve_hour=twelve_hour,
            )
        )
        position = match.end()
    rendered.append(format_string[position:])
    return "".join(rendered)


def _excel_datetime_token(
    value: datetime | date | time, token: str, *, minute_token: bool, twelve_hour: bool
) -> str:
    hour = value.hour if isinstance(value, (datetime, time)) else 0
    minute = value.minute if isinstance(value, (datetime, time)) else 0
    second = value.second if isinstance(value, (datetime, time)) else 0
    if minute_token:
        return f"{minute:02d}" if token == "mm" else str(minute)
    if token in {"am/pm", "a/p"}:
        if token == "a/p":
            return "A" if hour < 12 else "P"
        return "AM" if hour < 12 else "PM"
    display_hour = (hour % 12 or 12) if twelve_hour else hour
    if token == "hh":
        return f"{display_hour:02d}"
    if token == "h":
        return str(display_hour)
    if token == "ss":
        return f"{second:02d}"
    if token == "s":
        return str(second)
    if isinstance(value, time):
        return token
    replacements = {
        "yyyy": f"{value.year:04d}",
        "yy": f"{value.year % 100:02d}",
        "mmmm": value.strftime("%B"),
        "mmm": value.strftime("%b"),
        "mm": f"{value.month:02d}",
        "m": str(value.month),
        "dddd": value.strftime("%A"),
        "ddd": value.strftime("%a"),
        "dd": f"{value.day:02d}",
        "d": str(value.day),
    }
    return replacements[token]


def _apply_integer_mask(digits: str, mask: str) -> str:
    """Fill Excel digit placeholders right-to-left without dropping extra digits."""

    result: list[str] = []
    digit_index = len(digits) - 1
    for character in reversed(mask):
        if character in "0#":
            if digit_index >= 0:
                result.append(digits[digit_index])
                digit_index -= 1
            elif character == "0":
                result.append("0")
        elif character == ",":
            if digit_index >= 0 or result:
                result.append(character)
        else:
            result.append(character)
    if digit_index >= 0:
        result.extend(reversed(digits[: digit_index + 1]))
    return "".join(reversed(result))
