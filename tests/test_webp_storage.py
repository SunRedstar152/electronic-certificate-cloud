"""Real codec tests using temporary synthetic certificates only."""
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import date
from PIL import Image, ImageChops
import zxingcpp
from config import settings
from models import Certificate,Template
from services.image_builder import render_certificate, ImageRenderError
from services.certificate_images import save_image,image_text,image_options,png_export
from services.certificate_qr import update_certificate_qr,METADATA_KEY,QrUpdateError
from test_certificate_qr_update import fixture,CODE,OLD,NEW_BASE,NEW

class WebpStorageTests(unittest.TestCase):
    def test_roundtrip_metadata_pixels_and_repeated_qr_changes(self):
        with TemporaryDirectory() as directory:
            png=Path(directory)/"original.png"; webp=Path(directory)/"stored.webp"
            fixture(png,metadata=True)
            with Image.open(png) as original:
                text=image_text(original);text["credit"]="ทดสอบ ©"
                with webp.open("wb") as output:
                    save_image(original,output,file_format="WEBP",text=text,options={"dpi":(150,150)})
                with Image.open(webp) as stored:
                    self.assertEqual(stored.tobytes(),original.tobytes())
                    self.assertEqual(image_text(stored),text)
                    self.assertEqual(image_options(stored)["dpi"],(150,150))
                with webp.open("rb") as source, png_export(source) as exported, Image.open(exported) as restored:
                    self.assertEqual(restored.format,"PNG")
                    self.assertEqual(restored.tobytes(),original.tobytes())
                    self.assertEqual(image_text(restored),text)
                    self.assertAlmostEqual(restored.info["dpi"][0],150,places=1)
                before=original.copy()
            old_bytes=webp.read_bytes()
            self.assertEqual(update_certificate_qr(webp,CODE,NEW_BASE,dry_run=True),"updated")
            self.assertEqual(webp.read_bytes(),old_bytes)
            self.assertEqual(update_certificate_qr(webp,CODE,NEW_BASE),"updated")
            with Image.open(webp) as after, ImageChops.difference(before,after) as diff:
                self.assertEqual(after.format,"WEBP")
                metadata=json.loads(image_text(after)[METADATA_KEY])
                for region in metadata["regions"]: diff.paste((0,0,0),region["box"])
                self.assertIsNone(diff.getbbox())
                self.assertEqual(image_text(after)["credit"],"ทดสอบ ©")
                self.assertEqual([q.text for q in zxingcpp.read_barcodes(after)],[NEW])
            before.close()
            self.assertEqual(update_certificate_qr(webp,CODE,NEW_BASE),"unchanged")
            self.assertEqual(update_certificate_qr(webp,CODE,"https://third.example.test"),"updated")

    def test_failed_atomic_publication_preserves_webp(self):
        with TemporaryDirectory() as directory:
            png=Path(directory)/"original.png"; webp=Path(directory)/"stored.webp"
            fixture(png,metadata=True)
            with Image.open(png) as image,webp.open("wb") as output:
                save_image(image,output,file_format="WEBP",text=image_text(image))
            before=webp.read_bytes()
            with patch("services.certificate_qr.os.replace",side_effect=OSError("synthetic")):
                with self.assertRaises(QrUpdateError): update_certificate_qr(webp,CODE,NEW_BASE)
            self.assertEqual(before,webp.read_bytes())
            self.assertFalse(list(Path(directory).glob(".qr-update-*")))

    def test_zip_contains_real_png_with_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from zipfile import ZipFile
        from services.archive_builder import build_batch_archive
        from models import CertificateStatus
        with TemporaryDirectory() as directory,patch.object(settings,"CERTIFICATE_DIR",Path(directory)):
            png=Path(directory)/"source.png";webp=Path(directory)/"stored.webp"
            fixture(png,metadata=True)
            with Image.open(png) as image,webp.open("wb") as output:
                expected=image.tobytes();metadata=image_text(image)
                save_image(image,output,file_format="WEBP",text=metadata)
            certificate=SimpleNamespace(status=CertificateStatus.active,image_path=str(webp),recipient_reference="REF",verification_code=CODE)
            row=SimpleNamespace(certificate=certificate,recipient_reference="REF",row_number=1,id=1)
            db=Mock();db.scalar.return_value=SimpleNamespace(rows=[row])
            with patch("services.archive_builder.get_owned_batch"):
                archive=build_batch_archive(db,SimpleNamespace(organization_id=1),1)
            try:
                with ZipFile(archive.file) as zipped:
                    with Image.open(io.BytesIO(zipped.read(f"REF-{CODE}.png"))) as exported:
                        self.assertEqual(exported.format,"PNG")
                        self.assertEqual(exported.tobytes(),expected)
                        self.assertEqual(image_text(exported),metadata)
            finally: archive.close()

    def test_malformed_metadata_is_rejected(self):
        with Image.new("RGB",(10,10)) as image:
            output=io.BytesIO();image.save(output,format="WEBP",lossless=True,xmp=b"<!DOCTYPE x><x/>")
            with Image.open(io.BytesIO(output.getvalue())) as stored:
                with self.assertRaises(ValueError): image_text(stored)

    def test_large_dimension_rejected_without_png_fallback(self):
        with TemporaryDirectory() as directory,patch.object(settings,"TEMPLATE_DIR",Path(directory)):
            background=Path(directory)/"wide.png"
            with Image.new("RGB",(16384,2),"white") as image:image.save(background)
            template=Template(organization_id=1,background_image_path=str(background),layout={"version":2,"elements":[]})
            certificate=Certificate(organization_id=1,verification_code=CODE,recipient_name="Sample",topic="Test",issue_date=date(2026,9,20),custom_fields={})
            with self.assertRaises(ImageRenderError):
                render_certificate(template,certificate,Path(directory)/"issued.webp")
            self.assertFalse((Path(directory)/"issued.png").exists())
            self.assertFalse((Path(directory)/"issued.webp").exists())

if __name__=="__main__":unittest.main()
