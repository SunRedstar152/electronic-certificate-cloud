"""Initialize a fresh database from the main SQL file, then apply upgrades."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from database import engine


def main() -> None:
    try:
        with engine.connect() as connection:
            # Hold a session lock through both bootstrap and Alembic so two
            # migrate containers cannot change the schema at the same time.
            connection.execute(text("SELECT pg_advisory_lock(742619083)"))
            connection.commit()
            try:
                with connection.begin():
                    tables = set(inspect(connection).get_table_names(schema="public"))
                    if not tables:
                        script = (ROOT / "sql/schema.sql").read_text(encoding="utf-8")
                        # This connection owns the transaction; omit the file's
                        # outer transaction used for standalone SQL imports.
                        body = script.split("\nBEGIN;\n", 1)[1].rsplit("\nCOMMIT;", 1)[0]
                        connection.exec_driver_sql(body)
                        print("Initialized electronic certificate schema from sql/schema.sql.", flush=True)
                    elif "alembic_version" not in tables:
                        raise RuntimeError(
                            "The database already has tables but no alembic_version. "
                            "Review its schema before importing; existing data was not changed."
                        )
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True
                )
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(742619083)"))
                connection.commit()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
