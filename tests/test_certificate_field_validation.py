"""Field validation checks that run in memory, without a database."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

import routers.certificates as certificate_router
from models import Certificate, Template
from schemas import CertificateIssuanceRequest
from services.builtin_templates import original_definition
from services.certificates import (
    CertificateFieldInvalidError,
    normalize_certificate_fields,
)
from services.image_builder import render_certificate
from services.signatures import sample_signature


class CertificateFieldValidationTests(unittest.TestCase):
    def setUp(self):
        self.layout = original_definition()["layout"]
        self.field = next(
            item for item in self.layout["elements"] if item["kind"] == "date_range"
        )
        self.key = self.field["source"].removeprefix("custom.")

    def test_valid_date_ranges_preserve_dates(self):
        for start, end in [
            ("2026-09-10", "2026-09-10"),
            ("2026-09-10", "2026-09-12"),
            ("2026-12-31", "2027-01-01"),
            ("2028-02-29", "2028-03-01"),
        ]:
            with self.subTest(start=start, end=end):
                fields = {self.key: {"start": start, "end": end}}
                self.assertEqual(normalize_certificate_fields(self.layout, fields), fields)

    def test_blank_optional_range_is_omitted(self):
        self.assertEqual(normalize_certificate_fields(self.layout, {}), {})

    def test_invalid_ranges_identify_field_and_reason(self):
        for value, reason in [
            ({"start": "2026-09-12", "end": "2026-09-10"}, "range_reversed"),
            ({"start": "2026-09-10", "end": ""}, "range_incomplete"),
            ({"start": "", "end": "2026-09-10"}, "range_incomplete"),
            ({"start": "2026-09-10"}, "range_incomplete"),
            ({"start": "2026-02-30", "end": "2026-09-10"}, "date_invalid"),
        ]:
            with self.subTest(value=value):
                with self.assertRaises(CertificateFieldInvalidError) as caught:
                    normalize_certificate_fields(self.layout, {self.key: value})
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(caught.exception.field_key, self.key)
                self.assertEqual(caught.exception.field_label, self.field["label"])

    def test_text_error_identifies_the_correct_field(self):
        field = next(item for item in self.layout["elements"] if item["source"].startswith("custom.") and item["kind"] == "multiline")
        key = field["source"].removeprefix("custom.")
        with self.assertRaises(CertificateFieldInvalidError) as caught:
            normalize_certificate_fields(self.layout, {key: "a" * 2001})
        self.assertEqual(caught.exception.field_key, key)
        self.assertEqual(caught.exception.reason, "text_invalid")

    def test_time_ranges_use_the_same_field_context(self):
        self.field["kind"] = "time_range"
        self.field["format"]["pattern"] = "short"
        self.field.pop("sample_value", None)
        for start, end in [("09:00", "09:00"), ("09:00", "17:00")]:
            fields = {self.key: {"start": start, "end": end}}
            self.assertEqual(normalize_certificate_fields(self.layout, fields), fields)
        with self.assertRaises(CertificateFieldInvalidError) as caught:
            normalize_certificate_fields(self.layout, {self.key: {"start": "17:00", "end": "09:00"}})
        self.assertEqual(caught.exception.reason, "range_reversed")
        self.assertEqual(caught.exception.field_label, self.field["label"])

    def test_original_template_renders_valid_ranges_without_database_writes(self):
        definition = original_definition()
        template = Template(
            organization_id=1, layout=self.layout, dimensions=definition["dimensions"],
            positions=definition["positions"], background_image_path="builtin:original",
        )
        signatures = {
            item["id"]: sample_signature()
            for item in self.layout["elements"] if item["kind"] == "signature"
        }
        try:
            with TemporaryDirectory() as directory:
                for index, fields in enumerate([
                    {},
                    {self.key: {"start": "2026-09-10", "end": "2026-09-10"}},
                    {self.key: {"start": "2026-09-10", "end": "2026-09-12"}},
                ]):
                    certificate = Certificate(
                        organization_id=1, recipient_name="ตัวอย่างผู้รับ", topic="กิจกรรมตัวอย่าง",
                        issue_date=date(2026, 9, 10), verification_code="ABCD1234EFGH5678",
                        custom_fields=normalize_certificate_fields(self.layout, fields),
                    )
                    destination = render_certificate(template, certificate, Path(directory) / f"{index}.png", signatures=signatures)
                    with Image.open(destination) as image:
                        self.assertEqual(image.size, (2000, 1414))
                        self.assertEqual(image.format, "PNG")
        finally:
            for signature in signatures.values():
                signature.close()

    def test_api_returns_actionable_thai_error_without_submitted_values(self):
        fields = {self.key: {"start": "2026-09-12", "end": "2026-09-10"}}
        payload = CertificateIssuanceRequest(
            template_id=1, recipient_name="Recipient", topic="Activity",
            issue_date="2026-09-10", custom_fields=fields,
        )
        def validate(*args, **kwargs):
            return normalize_certificate_fields(self.layout, fields)
        with patch.object(certificate_router, "issue_certificate", side_effect=validate):
            with self.assertRaises(HTTPException) as caught:
                certificate_router.create_certificate(payload, db=None, current_user=None)
        error = caught.exception
        self.assertEqual(error.status_code, 422)
        self.assertEqual(error.detail["code"], "CERTIFICATE_FIELD_INVALID")
        self.assertIn(self.field["label"], error.detail["message"])
        self.assertIn("สิ้นสุด", error.detail["message"])
        self.assertEqual(error.detail["reason"], "range_reversed")
        self.assertNotIn("2026-09-12", str(error.detail))


if __name__ == "__main__":
    unittest.main()
