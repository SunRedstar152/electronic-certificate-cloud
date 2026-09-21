"""Create the one initial administrator without exposing a password."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import get_password_hash
from database import SessionLocal
from models import User, UserRole


_FIRST_ADMIN_LOCK_ID = 5_063_266_041_987_417_321


def validate_password_strength(password: str) -> None:
    """Reject passwords that do not meet the first-admin policy."""
    if (
        len(password) < 12
        or not any(character.islower() for character in password)
        or not any(character.isupper() for character in password)
        or not any(character.isdigit() for character in password)
        or not any(not character.isalnum() for character in password)
    ):
        raise click.UsageError(
            "Password must be at least 12 characters and include upper-case, "
            "lower-case, numeric, and symbol characters."
        )


def _password_from_environment_or_prompt(environment_variable: str) -> str:
    password = os.environ.get(environment_variable)
    if password is None:
        password = click.prompt("Password", hide_input=True)
    validate_password_strength(password)
    return password


@click.command()
@click.option("--username", required=True, metavar="USERNAME")
@click.option(
    "--password-env",
    default="FIRST_ADMIN_PASSWORD",
    show_default=True,
    metavar="VARIABLE",
    help="Read the password from this environment variable instead of prompting.",
)
def create_admin(username: str, password_env: str) -> None:
    """Create the first administrator; refuse if an administrator already exists."""
    normalized_username = username.strip()
    if not 3 <= len(normalized_username) <= 100:
        raise click.UsageError("Username must contain between 3 and 100 characters.")

    password = _password_from_environment_or_prompt(password_env)
    db = SessionLocal()
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _FIRST_ADMIN_LOCK_ID},
        )
        existing_admin = db.scalar(select(User.id).where(User.role == UserRole.admin))
        if existing_admin is not None:
            raise click.ClickException("An administrator already exists.")
        existing_username = db.scalar(
            select(User.id).where(User.username == normalized_username)
        )
        if existing_username is not None:
            raise click.ClickException("The requested username is already in use.")

        db.add(
            User(
                username=normalized_username,
                password_hash=get_password_hash(password),
                role=UserRole.admin,
                organization_id=None,
                is_active=True,
            )
        )
        db.commit()
    except click.ClickException:
        raise
    except Exception:
        db.rollback()
        raise click.ClickException("The administrator could not be created.")
    finally:
        db.close()

    click.echo("First administrator created.")


if __name__ == "__main__":
    create_admin()
