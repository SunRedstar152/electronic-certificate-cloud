import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy.engine import make_url
from scripts.container_entrypoint import main


ROOT = Path(__file__).resolve().parents[1]


def test_container_entrypoint_builds_an_encoded_url_and_preserves_arguments(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in dict(POSTGRES_USER="app", POSTGRES_PASSWORD="test:@/% value", POSTGRES_DB="certificates", POSTGRES_HOST="db").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["entrypoint", "uvicorn", "main:app", "two words"])
    observed = []
    monkeypatch.setattr(os, "execvp", lambda command, arguments: observed.append((command, arguments)))
    main()
    url = make_url(os.environ["DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "test:@/% value"
    assert url.host == "db"
    assert url.database == "certificates"
    assert observed == [("uvicorn", ["uvicorn", "main:app", "two words"])]


def test_container_entrypoint_requires_credentials_without_printing_environment():
    env = {key: value for key, value in os.environ.items() if key not in {"DATABASE_URL", "POSTGRES_PASSWORD"}}
    env["SECRET_KEY"] = "private-test-secret-never-print-this-value"
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/container_entrypoint.py"), sys.executable, "-c", "print('must-not-run')",
    ], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr
    assert "must-not-run" not in result.stdout
    assert env["SECRET_KEY"] not in result.stderr


def test_container_entrypoint_uses_explicit_database_url(monkeypatch):
    url = "postgresql+psycopg://external:password@remote.example/db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(sys, "argv", ["entrypoint", "alembic", "upgrade", "head"])
    monkeypatch.setattr(os, "execvp", lambda *_: None)
    main()
    assert os.environ["DATABASE_URL"] == url
