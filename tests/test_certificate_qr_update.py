"""Real PNG/QR checks, runnable with unittest without a database."""
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, PngImagePlugin
import qrcode
import zxingcpp


CODE = "ABCD1234EFGH5678"
OLD = "https://old.example.test/?code=ABCD1234EFGH5678"
NEW_BASE = "https://new.example.test/certificates"
NEW = "https://new.example.test/certificates/?code=ABCD1234EFGH5678"
KEY = "pkru.verification_qr"
BOX = (475, 280, 690, 495)


def fixture(path, *, metadata=False, url=OLD, box=BOX, other=False):
    image = Image.new("RGB", (760, 540), "#ddeeff")
    draw = ImageDraw.Draw(image)
    draw.text((30, 45), "Recipient - unchanged", fill="black")
    draw.line([(60, 380), (100, 340), (140, 400), (190, 350)], fill="navy", width=4)
    qr = qrcode.make(url).convert("RGB").resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.NEAREST)
    image.paste(qr, box[:2])
    qr.close()
    draw.text((box[0], box[3]+5), "ID: ABCD1234EFGH5678", fill="black")
    if other:
        with qrcode.make("https://unrelated.example.test/").convert("RGB") as extra:
            image.paste(extra.resize((180, 180), Image.Resampling.NEAREST), (20, 100))
    info = PngImagePlugin.PngInfo()
    info.add_text("description", "preserve this metadata")
    if metadata:
        info.add_text(KEY, json.dumps({"version": 1, "code": CODE, "regions": [{"box": list(box), "url": url}]}))
    image.save(path, pnginfo=info, dpi=(150, 150))
    image.close()


class CertificateQrUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("services.certificate_qr")

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "certificate.png"

    def assert_only_qr_changed(self, before, after, box=BOX):
        outside = ImageChops.difference(before, after)
        outside.paste((0, 0, 0), box)
        self.assertIsNone(outside.getbbox(), "Pixels outside the QR square changed")
        self.assertEqual(after.size, before.size)
        decoded = [result.text for result in zxingcpp.read_barcodes(after)]
        self.assertIn(NEW, decoded)
        self.assertNotIn(OLD, decoded)

    def test_changes_only_qr_and_keeps_metadata_and_dpi(self):
        fixture(self.path, metadata=True)
        with Image.open(self.path) as source:
            before = source.copy()
        result = self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.assertEqual(result, "updated")
        with Image.open(self.path) as after:
            self.assert_only_qr_changed(before, after)
            self.assertEqual(after.info["description"], "preserve this metadata")
            self.assertAlmostEqual(after.info["dpi"][0], 150, delta=.1)
            self.assertEqual(json.loads(after.info[KEY])["regions"][0]["url"], NEW)

    def test_legacy_detection_needs_no_template_and_leaves_other_qr_unchanged(self):
        fixture(self.path, other=True)
        with Image.open(self.path) as source:
            before = source.copy()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, NEW_BASE), "updated")
        with Image.open(self.path) as after:
            self.assert_only_qr_changed(before, after)
            self.assertIn("https://unrelated.example.test/", [r.text for r in zxingcpp.read_barcodes(after)])

    def test_same_url_is_byte_identical_and_repeated_change_uses_saved_box(self):
        fixture(self.path, metadata=True)
        before = self.path.read_bytes()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, "https://old.example.test/"), "unchanged")
        self.assertEqual(self.path.read_bytes(), before)
        self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.module.update_certificate_qr(self.path, CODE, "https://third.example.test")
        with Image.open(self.path) as image:
            self.assertIn("https://third.example.test/?code=ABCD1234EFGH5678", [r.text for r in zxingcpp.read_barcodes(image)])

    def test_dry_run_and_failed_publication_leave_original_bytes(self):
        fixture(self.path, metadata=True)
        before = self.path.read_bytes()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, NEW_BASE, dry_run=True), "updated")
        self.assertEqual(self.path.read_bytes(), before)
        with patch.object(self.module.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(self.module.QrUpdateError):
                self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_legacy_same_url_indexes_without_changing_pixels(self):
        fixture(self.path)
        with Image.open(self.path) as source:
            before = source.tobytes()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, "https://old.example.test"), "indexed")
        with Image.open(self.path) as after:
            self.assertEqual(after.tobytes(), before)
            self.assertIn(KEY, after.info)

    def test_cleanup_failure_keeps_the_controlled_error_and_original_file(self):
        fixture(self.path, metadata=True)
        before = self.path.read_bytes()
        with patch.object(self.module.os, "replace", side_effect=PermissionError("locked")), \
                patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            with self.assertRaises(self.module.QrUpdateError) as error:
                self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.assertEqual(error.exception.reason, "qr_update_failed")
        self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_code_or_unreadable_qr_never_rewrites(self):
        for url in ["https://old.example.test/?code=DIFFERENT", "not a verification URL"]:
            with self.subTest(url=url):
                fixture(self.path, url=url)
                before = self.path.read_bytes()
                with self.assertRaises(self.module.QrUpdateError):
                    self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
                self.assertEqual(self.path.read_bytes(), before)

    def test_all_verification_qrs_update_when_one_certificate_has_two(self):
        fixture(self.path)
        second_box = (20, 100, 200, 280)
        with Image.open(self.path) as source:
            image = source.copy()
        with qrcode.make(OLD).convert("RGB") as qr:
            image.paste(qr.resize((180, 180), Image.Resampling.NEAREST), second_box[:2])
        image.save(self.path)
        before = image.copy()
        image.close()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, NEW_BASE), "updated")
        with Image.open(self.path) as after, ImageChops.difference(before, after) as difference:
            self.assertEqual([r.text for r in zxingcpp.read_barcodes(after)].count(NEW), 2)
            for box in (BOX, second_box):
                difference.paste((0, 0, 0), box)
            self.assertIsNone(difference.getbbox())
        before.close()

    def test_source_changed_during_staging_is_not_overwritten(self):
        fixture(self.path, metadata=True)
        verify = self.module._verify_result
        replacement = b"new source written by another operation"
        def change_source(*args):
            verify(*args)
            self.path.write_bytes(replacement)
        with patch.object(self.module, "_verify_result", side_effect=change_source):
            with self.assertRaises(self.module.QrUpdateError) as error:
                self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.assertEqual(error.exception.reason, "source_changed")
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_generated_image_without_verification_qr_is_left_unchanged(self):
        with Image.new("RGB", (80, 60), "white") as image:
            image.save(self.path, pnginfo=self.module.issuance_png_info(image, CODE))
        before = self.path.read_bytes()
        self.assertEqual(self.module.update_certificate_qr(self.path, CODE, NEW_BASE), "unchanged")
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_coordinates_fail_without_touching_file(self):
        fixture(self.path, metadata=True)
        with Image.open(self.path) as source:
            info = PngImagePlugin.PngInfo()
            info.add_text(KEY, json.dumps({"version": 1, "code": CODE, "regions": [{"box": [0, 0, 760, 540], "url": OLD}]}))
            source.save(self.path.parent / "bad.png", pnginfo=info)
        self.path = self.path.parent / "bad.png"
        before = self.path.read_bytes()
        with self.assertRaises(self.module.QrUpdateError):
            self.module.update_certificate_qr(self.path, CODE, NEW_BASE)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_base_or_unscannable_long_url_preserves_original(self):
        fixture(self.path, metadata=True)
        before = self.path.read_bytes()
        for base in ["javascript:bad", "https://user:password@example.test", "https://example.test/?x=1", "https://example.test/#fragment", "https://example.test/" + "a"*3000]:
            with self.subTest(base=base[:45]):
                with self.assertRaises(self.module.QrUpdateError):
                    self.module.update_certificate_qr(self.path, CODE, base)
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
