import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings


TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]


def test_database_url_must_be_postgresql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///forbidden.db")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_key_rejects_development_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "change-me")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_key_is_required(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.delenv("SECRET_KEY")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_audit_retention_days_reads_a_valid_environment_value(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "90")

    settings = Settings(_env_file=None)

    assert settings.AUDIT_RETENTION_DAYS == 90


def test_scheduler_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ENABLE_SCHEDULER is False


def test_strict_raqm_check_is_opt_in_by_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.delenv("REQUIRE_RAQM", raising=False)

    settings = Settings(_env_file=None)

    assert settings.REQUIRE_RAQM is False


def test_scheduler_can_be_explicitly_enabled_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.setenv("ENABLE_SCHEDULER", "true")

    settings = Settings(_env_file=None)

    assert settings.ENABLE_SCHEDULER is True


def test_font_directory_has_a_safe_project_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.delenv("FONT_DIR", raising=False)

    settings = Settings(_env_file=None)

    assert settings.FONT_DIR == Path("storage/uploads/fonts")


def test_font_directory_can_be_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.setenv("FONT_DIR", str(tmp_path / "organization-fonts"))

    settings = Settings(_env_file=None)

    assert settings.FONT_DIR == tmp_path / "organization-fonts"


def test_audit_retention_days_reads_a_valid_dotenv_value_without_echoing_secrets(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"DATABASE_URL={TEST_DATABASE_URL}",
                "SECRET_KEY=dotenv-secret-key-that-is-at-least-32-characters",
                "AUDIT_RETENTION_DAYS=45",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.AUDIT_RETENTION_DAYS == 45
    assert "dotenv-secret-key" not in repr(settings)


@pytest.mark.parametrize("retention", ["0", "-1", "4001", "not-a-number"])
def test_audit_retention_days_rejects_invalid_values(monkeypatch, retention):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", retention)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "test-secret-key" not in str(error.value)


def test_scheduler_enable_flag_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.setenv("ENABLE_SCHEDULER", "sometimes")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
