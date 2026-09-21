"""Run with python -m scripts.update_certificate_qr (dry-run by default)."""
import argparse
import json

from services.qr_maintenance import run_qr_maintenance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check or update issued PNG verification QR codes using PUBLIC_BASE_URL.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate all changes without writing files (default).")
    mode.add_argument("--apply", action="store_true", help="Verify and replace QR images using the configured URL.")
    args = parser.parse_args(argv)
    report = run_qr_maintenance(dry_run=not args.apply)
    print(json.dumps(report.as_dict(), ensure_ascii=False))
    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
