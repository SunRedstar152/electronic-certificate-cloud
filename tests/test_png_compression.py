"""Real lossless issuance checks; no database or user images needed."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
import zxingcpp
from config import settings
from models import Certificate, Template
from services.builtin_templates import original_definition
from services.image_builder import render_certificate
from services.signatures import sample_signature
from services.certificate_qr import update_certificate_qr

class PngCompressionTests(unittest.TestCase):
    def test_smaller_original_certificate_preserves_pixels_metadata_and_qr_update(self):
        spec=original_definition()
        template=Template(organization_id=1,layout=spec["layout"],dimensions=spec["dimensions"],
            positions=spec["positions"],background_image_path="builtin:original")
        cert=Certificate(organization_id=1,recipient_name="ตัวอย่างผู้รับเกียรติบัตร",topic="กิจกรรมทดสอบประสิทธิภาพ",
            issue_date=date(2026,9,20),verification_code="ABCD1234EFGH5678",custom_fields={})
        signatures={item["id"]:sample_signature() for item in spec["layout"]["elements"] if item["kind"]=="signature"}
        try:
            with TemporaryDirectory() as directory, patch.object(settings,"PUBLIC_BASE_URL","https://old.example.test"):
                paths=[]
                for level in (6,9):
                    path=Path(directory)/f"level{level}.png"
                    with patch.object(settings,"PNG_COMPRESS_LEVEL",level):
                        render_certificate(template,cert,path,signatures=signatures)
                    paths.append(path)
                self.assertLess(paths[1].stat().st_size,paths[0].stat().st_size)
                with Image.open(paths[0]) as before, Image.open(paths[1]) as after:
                    self.assertEqual(before.size,after.size)
                    self.assertEqual(before.mode,after.mode)
                    self.assertEqual(before.tobytes(),after.tobytes())
                    self.assertEqual(before.text,after.text)
                    self.assertEqual([qr.text for qr in zxingcpp.read_barcodes(after)],
                                     ["https://old.example.test/?code=ABCD1234EFGH5678"])
                self.assertEqual(update_certificate_qr(paths[1],cert.verification_code,"https://new.example.test"),"updated")
                with Image.open(paths[1]) as updated:
                    self.assertEqual([qr.text for qr in zxingcpp.read_barcodes(updated)],
                                     ["https://new.example.test/?code=ABCD1234EFGH5678"])
                self.assertEqual(update_certificate_qr(paths[1],cert.verification_code,"https://new.example.test"),"unchanged")
        finally:
            for signature in signatures.values(): signature.close()

    def test_invalid_compression_cannot_modify_file(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/"unchanged.png"
            path.write_bytes(b"unchanged")
            with self.assertRaises(ValueError):
                update_certificate_qr(path,"CODE","https://example.test",compress_level=10)
            self.assertEqual(path.read_bytes(),b"unchanged")

if __name__ == "__main__": unittest.main()
