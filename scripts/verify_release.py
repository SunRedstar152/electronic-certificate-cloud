"""Run the maintained release checks on Windows, Linux, or inside Docker."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    test_url = os.environ.get("TEST_DATABASE_URL", "")
    secret = os.environ.get("SECRET_KEY", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")) or database_url != test_url:
        raise SystemExit("Set DATABASE_URL and TEST_DATABASE_URL to the same dedicated PostgreSQL test database.")
    if len(secret) < 32 or secret == "change-me":
        raise SystemExit("Set a non-default SECRET_KEY of at least 32 characters.")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for JavaScript checks.")
    run("Apply PostgreSQL migrations", [sys.executable, "-m", "alembic", "upgrade", "head"])
    run("Run Python tests", [sys.executable, "-m", "pytest", "tests", "-q"])
    run("Check Python dependencies", [sys.executable, "-m", "pip", "check"])
    run("Run JavaScript tests", [node, "--test", *[str(file) for file in sorted((ROOT / "tests/js").glob("*.test.js"))]])
    for file in sorted((ROOT / "static/js").glob("*.js")):
        run(f"Check syntax: {file.name}", [node, "--check", str(file)])
    run("Check Thai shaping", [sys.executable, "-c", "from services.text_layout import validate_thai_shaping_configuration; validate_thai_shaping_configuration()"])
    for family in ("Sarabun", "NotoSansThai", "THNiramitAS"):
        for weight in ("Regular", "Bold"):
            file = ROOT / "static/fonts" / f"{family}-{weight}.ttf"
            if not file.is_file() or file.stat().st_size == 0:
                raise SystemExit(f"Missing bundled font: {file.name}")
    unsafe = re.compile(r"Coming Soon|เน€เธ|เน\x83เธ|innerHTML\s*=")
    for file in (ROOT / "static").rglob("*"):
        if file.suffix in {".html", ".css", ".js"} and unsafe.search(file.read_text(encoding="utf-8")):
            raise SystemExit(f"Static content check failed: {file.relative_to(ROOT)}")
    print("\nRelease verification passed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
