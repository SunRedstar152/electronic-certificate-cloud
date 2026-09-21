"""Issuance metadata integration checks, with temporary images and no database."""
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops
import zxingcpp

from config import settings
from models import Certificate, Template
from services.certificate_qr import METADATA_KEY, update_certificate_qr
from services.image_builder import render_certificate


class QrRenderingTests(unittest.TestCase):
    def test_issued_png_carries_actual_qr_box_independent_of_later_template_edits(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            background = root / "background.png"
            with Image.new("RGB", (800, 600), "#f0f0ff") as image:
                image.save(background)
            template = Template(organization_id=1, background_image_path=str(background), layout={
                "version": 2, "elements": [{"id": "verification_qr", "source": "verification_url", "kind": "qr",
                "label": "QR", "visible": True, "box": {"x": .5, "y": .4, "width": .4, "height": .5}}],
            })
            certificate = Certificate(organization_id=1, verification_code="ABCD1234EFGH5678",
                                      recipient_name="Synthetic", topic="Testing", issue_date=date(2026, 9, 11), custom_fields={})
            path = root / "issued.png"
            with patch.object(settings, "TEMPLATE_DIR", root), patch.object(settings, "PUBLIC_BASE_URL", "https://old.example.test"):
                render_certificate(template, certificate, path)
            with Image.open(path) as source:
                metadata = json.loads(source.info[METADATA_KEY])
                self.assertEqual(metadata["code"], "ABCD1234EFGH5678")
                self.assertEqual(len(metadata["regions"]), 1)
                before = source.copy()
            template.layout["elements"][0]["box"] = {"x": 0, "y": 0, "width": .1, "height": .1}
            self.assertEqual(update_certificate_qr(path, certificate.verification_code, "https://new.example.test"), "updated")
            with Image.open(path) as after, ImageChops.difference(before, after) as difference:
                difference.paste((0, 0, 0), metadata["regions"][0]["box"])
                self.assertIsNone(difference.getbbox())
                self.assertEqual([r.text for r in zxingcpp.read_barcodes(after)], ["https://new.example.test/?code=ABCD1234EFGH5678"])
            before.close()


if __name__ == "__main__":
    unittest.main()
