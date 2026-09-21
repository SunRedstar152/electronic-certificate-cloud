"""Export the application's PostgreSQL schema without connecting to a database."""

from pathlib import Path
import argparse
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing model metadata creates an engine but never opens a connection.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://schema_export@localhost/pkru_certificate")
os.environ.setdefault("SECRET_KEY", "schema-export-only-no-authentication-or-network")

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_mock_engine
from models import Base


def export_schema(destination: Path) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if not revision:
        raise RuntimeError("No Alembic schema revision was found.")
    statements = []

    def collect(statement, *_args, **_kwargs):
        compiled = str(statement.compile(dialect=engine.dialect))
        statements.append("\n".join(line.rstrip() for line in compiled.splitlines()).strip() + ";")

    engine = create_mock_engine("postgresql+psycopg://", collect)
    Base.metadata.create_all(engine, checkfirst=False)
    statements.extend([
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));",
        "INSERT INTO alembic_version (version_num) VALUES ('" + revision.replace("'", "''") + "');",
    ])
    header = (
        "-- PKRU Electronic Certificate - PostgreSQL 18\n"
        "-- MAIN SQL FILE: sql/schema.sql\n"
        "-- Database: pkru_certificate (select/create it before importing).\n"
        "-- For an EMPTY database only. Contains schema only, no users or demo data.\n"
        "-- The Compose migrate service imports this file when the public schema is empty.\n"
        "-- Existing databases: use the Compose migrate service; do not import this again.\n"
        "-- Generated from models.py; schema revision: " + revision + "\n"
        "-- Regenerate: python scripts/export_schema.py\n\n"
        "BEGIN;\nSET LOCAL search_path TO public;\n\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(header + "\n\n".join(statements) + "\n\nCOMMIT;\n", encoding="utf-8")
    print(f"Wrote {destination.name}: {len(Base.metadata.tables)} application tables, revision {revision}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "sql/schema.sql")
    export_schema(parser.parse_args().output)
