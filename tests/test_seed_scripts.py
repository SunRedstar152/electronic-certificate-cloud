from click.testing import CliRunner
from pathlib import Path
import re
from sqlalchemy import select
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from auth import verify_password
from models import Certificate, CertificateStatus, Organization, Template, User, UserRole
import scripts.create_admin as create_admin_module


def _runner() -> CliRunner:
    return CliRunner()


def test_create_admin_is_idempotent_and_never_prints_password(
    db_session, monkeypatch
):
    """A duplicate-admin check or password echo would be a security regression."""
    monkeypatch.setattr(create_admin_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    first = _runner().invoke(
        create_admin_module.create_admin,
        ["--username", "first-admin"],
        input="StrongPass123!\n",
    )
    second = _runner().invoke(
        create_admin_module.create_admin,
        ["--username", "second-admin"],
        input="StrongPass123!\n",
    )

    assert first.exit_code == 0
    assert second.exit_code != 0
    assert "StrongPass123!" not in first.output + second.output
    account = db_session.scalar(select(User).where(User.username == "first-admin"))
    assert account is not None
    assert account.role == UserRole.admin
    assert account.organization_id is None
    assert verify_password("StrongPass123!", account.password_hash)


def test_create_admin_can_read_the_password_from_an_explicit_environment_variable(
    db_session, monkeypatch
):
    """Deployment automation must not need a password command-line argument."""
    monkeypatch.setattr(create_admin_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setenv("FIRST_ADMIN_PASSWORD", "StrongPass123!")

    result = _runner().invoke(
        create_admin_module.create_admin,
        ["--username", "environment-admin", "--password-env", "FIRST_ADMIN_PASSWORD"],
    )

    assert result.exit_code == 0
    assert "StrongPass123!" not in result.output
    account = db_session.scalar(select(User).where(User.username == "environment-admin"))
    assert account is not None
    assert verify_password("StrongPass123!", account.password_hash)


def test_concurrent_first_admin_creation_allows_exactly_one_administrator(
    monkeypatch,
):
    """Removing the transaction lock would let two concurrent bootstrap commands win."""
    barrier = Barrier(2)
    username_prefix = "concurrent-bootstrap"

    def synchronized_password(_environment_variable):
        barrier.wait(timeout=5)
        return "StrongPass123!"

    monkeypatch.setattr(
        create_admin_module,
        "_password_from_environment_or_prompt",
        synchronized_password,
    )

    def run(index):
        return _runner().invoke(
            create_admin_module.create_admin,
            ["--username", f"{username_prefix}-{index}"],
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        db = create_admin_module.SessionLocal()
        try:
            administrators = list(
                db.scalars(
                    select(User).where(
                        User.username.like(f"{username_prefix}-%")
                    )
                )
            )
        finally:
            db.close()
    finally:
        cleanup = create_admin_module.SessionLocal()
        try:
            cleanup.query(User).filter(
                User.username.like(f"{username_prefix}-%")
            ).delete(synchronize_session=False)
            cleanup.commit()
        finally:
            cleanup.close()

    assert sorted(result.exit_code for result in results) == [0, 1]
    assert len(administrators) == 1
    assert administrators[0].role == UserRole.admin


def test_demo_seed_creates_a_renderable_active_certificate(
    db_session, monkeypatch, tmp_path
):
    """A demo fixture is useful only when its image and certificate are usable."""
    import scripts.seed_demo as seed_demo_module

    monkeypatch.setattr(seed_demo_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(seed_demo_module.settings, "TEMPLATE_DIR", tmp_path / "templates")
    monkeypatch.setattr(
        seed_demo_module.settings, "CERTIFICATE_DIR", tmp_path / "certificates"
    )
    monkeypatch.setenv("DEMO_ORGANIZATION_PASSWORD", "AnotherStrong123!")
    db_session.add(
        User(
            username="demo-seed-admin",
            password_hash="not-used-by-this-command",
            role=UserRole.admin,
            organization_id=None,
            is_active=True,
        )
    )
    db_session.flush()

    result = _runner().invoke(
        seed_demo_module.seed_demo,
        ["--admin-username", "demo-seed-admin", "--organization-username", "demo-org"],
    )

    assert result.exit_code == 0
    assert "AnotherStrong123!" not in result.output
    organization = db_session.scalar(
        select(Organization).where(Organization.name == "Demo Organization")
    )
    assert organization is not None
    assert re.fullmatch(r"ORG[A-Z0-9]{13}", organization.code)
    account = db_session.scalar(
        select(User).where(User.organization_id == organization.id)
    )
    assert account is not None
    assert account.role == UserRole.organization
    template = db_session.scalar(
        select(Template).where(Template.organization_id == organization.id)
    )
    assert template is not None
    assert template.background_image_path.endswith(".png")
    certificate = db_session.scalar(
        select(Certificate).where(Certificate.organization_id == organization.id)
    )
    assert certificate is not None
    assert certificate.status == CertificateStatus.active
    assert certificate.image_path is not None
    assert Path(certificate.image_path).is_file()
