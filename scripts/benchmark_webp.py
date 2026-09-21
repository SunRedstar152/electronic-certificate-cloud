"""Reproducible synthetic benchmark. No database connection or user files."""
import argparse
import io
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
os.environ["SECRET_KEY"] = "synthetic-benchmark-not-a-real-secret-123456"
os.environ["PUBLIC_BASE_URL"] = "https://benchmark.example.test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import zxingcpp
from models import Template, Certificate
from services.builtin_templates import original_definition
from services.image_builder import render_certificate
from services.signatures import sample_signature
from services.certificate_images import save_image, image_text, png_export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, choices=range(1,11))
    args = parser.parse_args()
    spec = original_definition()
    template = Template(organization_id=1, layout=spec["layout"], dimensions=spec["dimensions"],
                        positions=spec["positions"], background_image_path="builtin:original")
    certificate = Certificate(organization_id=1, recipient_name="ตัวอย่างผู้รับเกียรติบัตร",
        topic="กิจกรรมทดสอบประสิทธิภาพ", issue_date=date(2026,9,20),
        verification_code="ABCD1234EFGH5678", custom_fields={})
    signatures = {item["id"]: sample_signature() for item in spec["layout"]["elements"] if item["kind"]=="signature"}
    results = []
    try:
        with TemporaryDirectory() as directory:
            original = render_certificate(template, certificate, Path(directory)/"sample.png", signatures=signatures)
            with Image.open(original) as source:
                source.load()
                text = image_text(source)
                codes = [qr.text for qr in zxingcpp.read_barcodes(source)]
                for fmt,method in [("PNG",4),("WEBP",0),("WEBP",1),("WEBP",3),("WEBP",4),("WEBP",6)]:
                    timings = []
                    for _ in range(args.runs):
                        output = io.BytesIO()
                        start = time.perf_counter()
                        save_image(source, output, file_format=fmt, text=text, compress_level=9, webp_method=method)
                        timings.append(time.perf_counter()-start)
                    payload = output.getvalue()
                    with Image.open(io.BytesIO(payload)) as decoded:
                        assert decoded.size == source.size
                        assert decoded.convert("RGB").tobytes() == source.tobytes()
                        assert image_text(decoded) == text
                        assert [qr.text for qr in zxingcpp.read_barcodes(decoded)] == codes
                    with png_export(io.BytesIO(payload)) as exported, Image.open(exported) as downloaded:
                        assert downloaded.tobytes() == source.tobytes()
                        assert image_text(downloaded) == text
                    results.append({"format":fmt,"method":method if fmt=="WEBP" else None,
                        "bytes_including_metadata":len(payload),
                        "median_encode_ms":round(statistics.median(timings)*1000,2),
                        "GB_for_2000000":len(payload)*2000000/1e9})
    finally:
        for signature in signatures.values():
            signature.close()
    print(json.dumps({"dimensions":spec["dimensions"],"runs":args.runs,
                      "pixels_metadata_qr_and_png_export_verified":True,"results":results},indent=2))


if __name__ == "__main__":
    main()
