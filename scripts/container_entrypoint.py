"""Build a database URL safely, then replace this process with the given command."""

import os
import sys

from sqlalchemy.engine import URL


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        password = os.environ.get("POSTGRES_PASSWORD")
        if not password:
            raise SystemExit("Set DATABASE_URL or POSTGRES_PASSWORD before starting the container.")
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+psycopg",
            username=os.environ.get("POSTGRES_USER", "certificate_app"),
            password=password,
            host=os.environ.get("POSTGRES_HOST", "db"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ.get("POSTGRES_DB", "pkru_certificate"),
        ).render_as_string(hide_password=False)
    if len(sys.argv) < 2:
        raise SystemExit("Provide a command to run in the container.")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
