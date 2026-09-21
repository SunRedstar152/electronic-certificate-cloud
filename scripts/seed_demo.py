"""Create explicitly requested, non-secret demonstration data."""

from __future__ import annotations

from datetime import date
from io import BytesIO
import os
import sys
from pathlib import Path

import click
from PIL import Image
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from database import SessionLocal
from models import Organization, RecordStatus, Template, User, UserRole
from schemas import CertificateCreate, OrganizationCreate
from services.accounts import create_organization_account
from services.certificates import create_certificate
from services.template_files import store_template_image, validate_template_image
from scripts.create_admin import validate_password_strength


_DEMO_ORGANIZATION_NAME = "Demo Organization"
_DEMO_POSITIONS = {
    "name": {"x": 0.5, "y": 0.42},
    "topic": {"x": 0.5, "y": 0.55},
    "issue_date": {"x": 0.3, "y": 0.78},
    "qr": {"x": 0.82, "y": 0.76},
}


def _secret_from_environment_or_prompt(environment_variable: str, label: str) -> str:
    secret = os.environ.get(environment_variable)
    if secret is None:
        secret = click.prompt(label, hide_input=True)
    validate_password_strength(secret)
    return secret


def _demo_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1200, 850), "white").save(output, format="PNG")
    return output.getvalue()


@click.command()
@click.option("--admin-username", required=True, metavar="USERNAME")
@click.option("--organization-username", required=True, metavar="USERNAME")
@click.option(
    "--organization-password-env",
    default="DEMO_ORGANIZATION_PASSWORD",
    show_default=True,
)
def seed_demo(
    admin_username: str,
    organization_username: str,
    organization_password_env: str,
) -> None:
    """Create one demo organization, account, template, and active certificate."""
    organization_password = _secret_from_environment_or_prompt(
        organization_password_env, "Demo organization password"
    )
    db = SessionLocal()
    image_path: Path | None = None
    try:
        if db.scalar(
            select(Organization.id).where(Organization.name == _DEMO_ORGANIZATION_NAME)
        ) is not None:
            raise click.ClickException("The demo organization already exists.")

        admin = db.scalar(
            select(User).where(
                User.username == admin_username.strip(),
                User.role == UserRole.admin,
                User.is_active.is_(True),
            )
        )
        if admin is None:
            raise click.ClickException(
                "An active administrator is required before seeding demo data."
            )
        organization, account = create_organization_account(
            db,
            OrganizationCreate(
                name=_DEMO_ORGANIZATION_NAME,
                contact_name="Demo Contact",
                contact_email="demo@example.invalid",
                contact_phone=None,
                username=organization_username.strip(),
                password=organization_password,
            ),
            admin,
        )
        image_path = store_template_image(
            validate_template_image(_demo_image(), "demo-certificate.png")
        )
        template = Template(
            organization_id=organization.id,
            name="Demo Certificate Template",
            background_image_path=image_path.as_posix(),
            positions=_DEMO_POSITIONS,
            dimensions={"width": 1200, "height": 850},
            status=RecordStatus.active,
        )
        db.add(template)
        db.flush()
        create_certificate(
            db,
            account,
            CertificateCreate(
                template_id=template.id,
                recipient_reference="DEMO-001",
                recipient_name="Demo Recipient",
                topic="Demo Certificate",
                issue_date=date.today(),
            ),
            idempotency_key="demo-certificate-v1",
        )
    except click.ClickException:
        raise
    except Exception:
        db.rollback()
        if image_path is not None:
            image_path.unlink(missing_ok=True)
        raise click.ClickException("Demo data could not be created.")
    finally:
        db.close()

    click.echo("Demo data created.")


if __name__ == "__main__":
    seed_demo()
