from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.encoders import jsonable_encoder

from services.import_parser import (
    IMPORT_DUPLICATE_REFERENCE,
    IMPORT_REQUIRED_VALUE,
    IMPORT_VALUE_TOO_LONG,
    ImportParseError,
    MAX_CELL_LENGTH,
    map_rows,
    ParsedTable,
    ParsedRow,
    parse_xlsx,
)
from services.template_layout import CustomFieldSpec


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def mapping_table(text: str) -> ParsedTable:
    lines = text.splitlines()
    headers = tuple(lines[0].split("\t"))
    return ParsedTable(headers, tuple(
        ParsedRow(index, dict(zip(headers, line.split("\t"))))
        for index, line in enumerate(lines[1:], start=2)
    ))


@pytest.mark.parametrize(
    ("parser", "source"),
    [
        (parse_xlsx, lambda: load_fixture("recipients.xlsx")),
    ],
)
def test_parsers_preserve_leading_zero_recipient_ids(parser, source):
    table = parser(source())

    rows = map_rows(
        table,
        {
            "recipient_reference": "recipient_id",
            "recipient_name": "name",
            "department": "department",
        },
    )

    assert rows[0].row_number == 2
    assert rows[0].recipient_reference == "001"
    assert rows[0].recipient_name == "สมชาย ใจดี"
    assert rows[0].custom_fields == {"department": "IT"}
    assert rows[0].errors == ()


def test_xlsx_preserves_numeric_cells_using_their_display_format():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["recipient_id", "name"])
    worksheet.append([1, "Ada Lovelace"])
    worksheet["A2"].number_format = "000"
    stream = BytesIO()
    workbook.save(stream)

    table = parse_xlsx(stream.getvalue())
    rows = map_rows(table, {"recipient_reference": "recipient_id", "recipient_name": "name"})

    assert rows[0].recipient_reference == "001"


def test_xlsx_preserves_common_excel_display_formats():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["reference", "percent", "issued"])
    worksheet.append([12345, 0.125, date(2026, 8, 12)])
    worksheet["A2"].number_format = "000-00"
    worksheet["B2"].number_format = "0.0%"
    worksheet["C2"].number_format = "dd/mm/yyyy"
    stream = BytesIO()
    workbook.save(stream)

    table = parse_xlsx(stream.getvalue())

    assert table.rows[0].values == {
        "reference": "123-45",
        "percent": "12.5%",
        "issued": "12/08/2026",
    }


def test_xlsx_preserves_datetime_minutes_and_padded_numeric_masks():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["issued", "amount", "long_reference", "optional"])
    worksheet.append([datetime(2026, 8, 12, 9, 5), 1.2, 123456, 12])
    worksheet["A2"].number_format = "dd/mm/yyyy hh:mm"
    worksheet["B2"].number_format = "000.00"
    worksheet["C2"].number_format = "000-00"
    worksheet["D2"].number_format = "###"
    stream = BytesIO()
    workbook.save(stream)

    values = parse_xlsx(stream.getvalue()).rows[0].values

    assert values == {
        "issued": "12/08/2026 09:05",
        "amount": "001.20",
        "long_reference": "1234-56",
        "optional": "12",
    }


def test_xlsx_formats_compact_datetime_with_quoted_separator():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["issued"])
    worksheet.append([datetime(2026, 8, 12, 9, 5)])
    worksheet["A2"].number_format = 'yyyy-mm-dd"T"hh:mm'
    stream = BytesIO()
    workbook.save(stream)

    assert parse_xlsx(stream.getvalue()).rows[0].values["issued"] == "2026-08-12T09:05"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(12, "12"), (1234, "1,234"), (-1234.5, "-1,234.50")],
)
def test_xlsx_numeric_grouping_omits_unneeded_leading_separator(value, expected):
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["amount"])
    worksheet.append([value])
    worksheet["A2"].number_format = "#,##0" if isinstance(value, int) else "#,##0.00"
    stream = BytesIO()
    workbook.save(stream)

    assert parse_xlsx(stream.getvalue()).rows[0].values["amount"] == expected


def test_xlsx_grouped_mandatory_zero_mask_preserves_all_padding():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["reference"])
    worksheet.append([123])
    worksheet["A2"].number_format = "000,000"
    stream = BytesIO()
    workbook.save(stream)

    assert parse_xlsx(stream.getvalue()).rows[0].values["reference"] == "000,123"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(9, "9:05 AM"), (21, "9:05 PM")],
)
def test_xlsx_formats_am_pm_as_an_atomic_token(hour, expected):
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["issued"])
    worksheet.append([datetime(2026, 8, 12, hour, 5)])
    worksheet["A2"].number_format = "h:mm AM/PM"
    stream = BytesIO()
    workbook.save(stream)

    assert parse_xlsx(stream.getvalue()).rows[0].values["issued"] == expected


def test_xlsx_unsupported_numeric_format_returns_safe_raw_value():
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["value"])
    worksheet.append([1.25])
    worksheet["A2"].number_format = "[Blue]0.0E+00"
    stream = BytesIO()
    workbook.save(stream)

    assert parse_xlsx(stream.getvalue()).rows[0].values["value"] == "1.25"


def _xlsx_like_archive(entries: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_xlsx_preflight_rejects_excess_archive_entries_before_openpyxl(monkeypatch):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_ARCHIVE_ENTRIES", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive complex archives"),
    )
    source = _xlsx_like_archive({"a.xml": b"<x/>", "b.xml": b"<x/>", "c.xml": b"<x/>"})

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


@pytest.mark.parametrize(
    ("member_name", "xml", "limit_name"),
    [
        (
            "xl/sharedStrings.xml",
            b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si/><si/><si/></sst>',
            "MAX_XLSX_SHARED_STRINGS",
        ),
        (
            "xl/styles.xml",
            b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf/><xf/><xf/></cellXfs></styleSheet>',
            "MAX_XLSX_STYLE_RECORDS",
        ),
    ],
)
def test_xlsx_preflight_rejects_high_object_xml_before_openpyxl(
    monkeypatch, member_name, xml, limit_name
):
    monkeypatch.setattr(f"services.import_parser.{limit_name}", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive complex XML"),
    )
    source = _xlsx_like_archive({member_name: xml})

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


def test_xlsx_preflight_discovers_alternate_shared_strings_part(monkeypatch):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_SHARED_STRINGS", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive complex XML"),
    )
    source = _xlsx_like_archive(
        {
            "[Content_Types].xml": b"""<?xml version="1.0"?>
                <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                  <Override PartName="/custom/text-pool.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
                </Types>""",
            "custom/text-pool.xml": b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si/><si/><si/></sst>',
        }
    )

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


def test_xlsx_preflight_resolves_alternate_worksheet_relationship_target(monkeypatch):
    monkeypatch.setattr("services.import_parser.MAX_IMPORT_ROWS", 1)
    monkeypatch.setattr("services.import_parser.MAX_IMPORT_COLUMNS", 1)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive complex XML"),
    )
    source = _xlsx_like_archive(
        {
            "xl/workbook.xml": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            "xl/_rels/workbook.xml.rels": b"""<?xml version="1.0"?>
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="../custom/sheet-a.xml"/>
                </Relationships>""",
            "custom/sheet-a.xml": b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c/><c/><c/></row></sheetData></worksheet>',
        }
    )

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


@pytest.mark.parametrize("style_tag", ["font", "fill", "border", "numFmt"])
def test_xlsx_preflight_caps_each_eager_style_collection(monkeypatch, style_tag):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_STYLE_OBJECTS_PER_TYPE", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive style amplification"),
    )
    plural = {"font": "fonts", "fill": "fills", "border": "borders", "numFmt": "numFmts"}[style_tag]
    styles = (
        f'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<{plural}><{style_tag}/><{style_tag}/><{style_tag}/></{plural}></styleSheet>'
    ).encode()
    source = _xlsx_like_archive(
        {
            "[Content_Types].xml": b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Override PartName="/custom/look.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
            </Types>""",
            "custom/look.xml": styles,
        }
    )

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


def test_xlsx_preflight_rejects_oversized_number_format_before_openpyxl(monkeypatch):
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive oversized formats"),
    )
    repeated_date_tokens = b"m" * 4_096
    styles = (
        b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<numFmts count="1"><numFmt numFmtId="164" formatCode="'
        + repeated_date_tokens
        + b'"/></numFmts></styleSheet>'
    )
    source = _xlsx_like_archive({"xl/styles.xml": styles})

    with pytest.raises(ImportParseError) as error:
        parse_xlsx(source)

    assert error.value.code == "IMPORT_FILE_TOO_COMPLEX"


def test_xlsx_preflight_caps_content_type_overrides_before_openpyxl(monkeypatch):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_METADATA_OBJECTS", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive amplified metadata"),
    )
    overrides = b"".join(
        f'<Override PartName="/custom/{index}.xml" ContentType="application/xml"/>'.encode()
        for index in range(3)
    )
    source = _xlsx_like_archive(
        {
            "[Content_Types].xml": (
                b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                + overrides
                + b"</Types>"
            )
        }
    )

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


def test_xlsx_preflight_caps_relationship_objects_before_openpyxl(monkeypatch):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_METADATA_OBJECTS", 2)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive amplified relationships"),
    )
    relationships = b"".join(
        f'<Relationship Id="r{index}" Type="urn:test" Target="part-{index}.xml"/>'.encode()
        for index in range(3)
    )
    source = _xlsx_like_archive(
        {
            "xl/_rels/workbook.xml.rels": (
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + relationships
                + b"</Relationships>"
            )
        }
    )

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


@pytest.mark.parametrize(
    "metadata_name",
    ["[Content_Types].xml", "xl/_rels/workbook.xml.rels"],
)
def test_xlsx_preflight_caps_metadata_member_bytes(monkeypatch, metadata_name):
    monkeypatch.setattr("services.import_parser.MAX_XLSX_METADATA_BYTES", 64)
    monkeypatch.setattr(
        "services.import_parser.load_workbook",
        lambda *args, **kwargs: pytest.fail("openpyxl must not receive oversized metadata"),
    )
    source = _xlsx_like_archive({metadata_name: b"<root>" + b" " * 100 + b"</root>"})

    with pytest.raises(ImportParseError, match="IMPORT_FILE_TOO_COMPLEX"):
        parse_xlsx(source)


def test_xlsx_rejects_invalid_workbook_bytes_with_a_stable_error():
    with pytest.raises(ImportParseError, match="IMPORT_INVALID_XLSX"):
        parse_xlsx(b"not an xlsx workbook")


def test_xlsx_rejects_more_than_5000_data_rows():
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet()
    worksheet.append(["recipient_id"])
    for row_number in range(5001):
        worksheet.append([str(row_number)])
    stream = BytesIO()
    workbook.save(stream)

    with pytest.raises(ImportParseError, match="IMPORT_ROW_LIMIT_EXCEEDED"):
        parse_xlsx(stream.getvalue())


def test_xlsx_rejects_more_than_30_columns():
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append([f"column_{index}" for index in range(31)])
    stream = BytesIO()
    workbook.save(stream)

    with pytest.raises(ImportParseError, match="IMPORT_COLUMN_LIMIT_EXCEEDED"):
        parse_xlsx(stream.getvalue())


def test_mapping_rejects_duplicate_target_fields_after_normalization():
    table = mapping_table("recipient_id\tname\n001\tAda\n")

    with pytest.raises(ImportParseError, match="IMPORT_INVALID_MAPPING"):
        map_rows(
            table,
            {
                "recipient_reference": "recipient_id",
                " recipient_reference ": "recipient_id",
                "recipient_name": "name",
            },
        )


def test_mapping_returns_stable_errors_for_missing_duplicate_and_too_long_values():
    table = mapping_table(
        "recipient_id\tname\n"
        "001\tAda\n"
        "001\tGrace\n"
        "\tLinus\n"
        f"003\t{'N' * 256}\n"
    )

    rows = map_rows(table, {"recipient_reference": "recipient_id", "recipient_name": "name"})

    assert rows[0].valid
    assert [error.code for error in rows[1].errors] == [IMPORT_DUPLICATE_REFERENCE]
    assert [error.code for error in rows[2].errors] == [IMPORT_REQUIRED_VALUE]
    assert [error.code for error in rows[3].errors] == [IMPORT_VALUE_TOO_LONG]
    assert all(error.message.isascii() for row in rows for error in row.errors)


def test_mapping_reports_each_missing_required_value():
    table = mapping_table("recipient_id\tname\n\t\n")

    row = map_rows(table, {"recipient_reference": "recipient_id", "recipient_name": "name"})[0]

    assert [error.field for error in row.errors] == ["recipient_reference", "recipient_name"]
    assert [error.code for error in row.errors] == [IMPORT_REQUIRED_VALUE, IMPORT_REQUIRED_VALUE]


def test_normalized_rows_serialize_for_batch_preview():
    table = mapping_table("recipient_id\tname\tdepartment\n001\tAda\tIT\n")
    row = map_rows(
        table,
        {
            "recipient_reference": "recipient_id",
            "recipient_name": "name",
            "department": "department",
        },
    )[0]

    assert asdict(row)["custom_fields"] == {"department": "IT"}
    assert jsonable_encoder(row)["custom_fields"] == {"department": "IT"}


def test_template_range_field_maps_start_and_end_columns():
    table = mapping_table(
        "name\tstart_date\tend_date\n"
        "สมชาย ใจดี\t2026-08-13\t2026-08-15\n"
    )
    rows = map_rows(
        table,
        {
            "recipient_name": "name",
            "recipient_reference": "name",
            "custom.training_period.start": "start_date",
            "custom.training_period.end": "end_date",
        },
        field_specs=(
            CustomFieldSpec(
                key="training_period",
                label="ระยะเวลาอบรม",
                kind="date_range",
                required=True,
            ),
        ),
    )

    assert rows[0].custom_fields["training_period"] == {
        "start": "2026-08-13",
        "end": "2026-08-15",
    }


def test_template_mapping_rejects_custom_target_not_defined_by_layout():
    table = mapping_table("recipient_id\tname\tsecret\n001\tAda\tvalue\n")

    with pytest.raises(ImportParseError, match="IMPORT_INVALID_MAPPING"):
        map_rows(
            table,
            {
                "recipient_reference": "recipient_id",
                "recipient_name": "name",
                "custom.secret": "secret",
            },
            field_specs=(
                CustomFieldSpec(
                    key="department",
                    label="Department",
                    kind="text",
                    required=False,
                ),
            ),
        )
