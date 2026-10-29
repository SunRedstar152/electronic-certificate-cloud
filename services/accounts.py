from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_password_hash
from core.errors import api_error
from models import Organization, RecordStatus, User, UserRole
from schemas import OrganizationCreate, OrganizationUpdate, PersonnelCreate
from services.audit import record_audit
from services.builtin_templates import ensure_original_template
from services.organization_identity import (
    generate_organization_code,
    normalize_organization_name,
)


MAX_ORGANIZATION_CODE_ATTEMPTS = 5


def _organization_not_found():
    return api_error(404, "ORG_NOT_FOUND", "Organization not found.")


def _organization_for_update(db: Session, organization_id: int) -> Organization:
    organization = db.scalar(select(Organization).where(
        Organization.id == organization_id, Organization.deleted_at.is_(None)
    ).with_for_update().execution_options(populate_existing=True))
    if organization is None:
        raise _organization_not_found()
    return organization


def _organization_user_or_404(
    db: Session, organization_id: int, user_id: int
) -> User:
    _organization_for_update(db, organization_id)
    account = db.scalar(
        select(User).where(
            User.deleted_at.is_(None),
            User.id == user_id,
            User.organization_id == organization_id,
            User.role == UserRole.organization,
        ).execution_options(populate_existing=True)
    )
    if account is None:
        raise api_error(404, "ACCOUNT_NOT_FOUND", "Personnel account not found.")
    return account


def _constraint_name(exc: IntegrityError) -> str:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""


def _raise_duplicate(exc: IntegrityError) -> None:
    constraint_name = _constraint_name(exc)
    if constraint_name == "uq_organizations_name_ci":
        raise api_error(409, "ORG_NAME_EXISTS", "Organization name already exists.")
    if constraint_name == "uq_users_username":
        raise api_error(
            409,
            "ACCOUNT_USERNAME_EXISTS",
            "Account username already exists.",
        )
    raise exc


def create_organization_account(
    db: Session,
    data: OrganizationCreate,
    actor: User,
) -> tuple[Organization, User]:
    normalized_name = normalize_organization_name(data.name)
    if not normalized_name:
        raise api_error(
            422,
            "REQUEST_VALIDATION_ERROR",
            "The request contains invalid or missing fields.",
        )
    if db.scalar(
        select(Organization.id).where(
            func.lower(Organization.name) == normalized_name.lower()
        )
    ):
        raise api_error(409, "ORG_NAME_EXISTS", "Organization name already exists.")
    if db.scalar(select(User.id).where(User.username == data.username)):
        raise api_error(
            409,
            "ACCOUNT_USERNAME_EXISTS",
            "Account username already exists.",
        )

    for _attempt in range(MAX_ORGANIZATION_CODE_ATTEMPTS):
        organization = Organization(
            name=normalized_name,
            code=generate_organization_code(),
            contact_name=data.contact_name,
            contact_email=data.contact_email,
            contact_phone=data.contact_phone,
            status=RecordStatus.active,
        )
        account = User(
            username=data.username,
            password_hash=get_password_hash(data.password),
            role=UserRole.organization,
            organization=organization,
            is_active=True,
        )
        try:
            with db.begin_nested():
                db.add_all([organization, account])
                db.flush()
                ensure_original_template(db, organization.id)
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_organizations_code":
                continue
            _raise_duplicate(exc)

        audit = record_audit(
            db,
            actor,
            "organization.create",
            "organization",
            organization.id,
            {"username": account.username},
        )
        audit.organization_id = organization.id
        return organization, account

    raise api_error(
        500,
        "ORG_CODE_GENERATION_FAILED",
        "Internal organization code could not be generated.",
    )


def update_organization(
    db: Session,
    organization_id: int,
    data: OrganizationUpdate,
    actor: User,
) -> Organization:
    organization = _organization_for_update(db, organization_id)

    values = data.model_dump(exclude_unset=True)
    if any(
        field in data.model_fields_set and getattr(data, field) is None
        for field in ("name", "status")
    ):
        raise api_error(
            422,
            "REQUEST_VALIDATION_ERROR",
            "The request contains invalid or missing fields.",
        )
    requested_status = values.pop("status", None)

    if "name" in values:
        values["name"] = normalize_organization_name(values["name"])
        if not values["name"]:
            raise api_error(
                422,
                "REQUEST_VALIDATION_ERROR",
                "The request contains invalid or missing fields.",
            )
        duplicate_id = db.scalar(
            select(Organization.id).where(
                func.lower(Organization.name) == values["name"].lower(),
                Organization.id != organization.id,
            )
        )
        if duplicate_id is not None:
            raise api_error(409, "ORG_NAME_EXISTS", "Organization name already exists.")

    changed_fields = {
        key: value
        for key, value in values.items()
        if getattr(organization, key) != value
    }
    status_changed = (
        requested_status is not None and organization.status != requested_status
    )
    previous_status = organization.status

    try:
        with db.begin_nested():
            for key, value in changed_fields.items():
                setattr(organization, key, value)
            if status_changed:
                organization.status = requested_status
            db.flush()
    except IntegrityError as exc:
        _raise_duplicate(exc)

    if changed_fields:
        audit = record_audit(
            db,
            actor,
            "organization.update",
            "organization",
            organization.id,
            {"fields": sorted(changed_fields)},
        )
        audit.organization_id = organization.id
    if status_changed:
        audit = record_audit(
            db,
            actor,
            "organization.status_update",
            "organization",
            organization.id,
            {
                "from": previous_status.value,
                "to": requested_status.value,
            },
        )
        audit.organization_id = organization.id

    return organization


def create_organization_user(
    db: Session,
    organization_id: int,
    data: PersonnelCreate,
    actor: User,
    correlation_id: str | None = None,
) -> User:
    organization = _organization_for_update(db, organization_id)

    account = User(
        username=data.username,
        password_hash=get_password_hash(data.password),
        role=UserRole.organization,
        organization_id=organization.id,
        is_active=True,
    )
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError as exc:
        _raise_duplicate(exc)

    audit = record_audit(
        db,
        actor,
        "personnel.create",
        "account",
        account.id,
        {"username": account.username},
    )
    audit.organization_id = organization.id
    audit.correlation_id = correlation_id
    return account


def set_organization_user_status(
    db: Session,
    organization_id: int,
    user_id: int,
    is_active: bool,
    actor: User,
    correlation_id: str | None = None,
) -> User:
    account = _organization_user_or_404(db, organization_id, user_id)
    previous = account.is_active

    if previous and not is_active:
        active_accounts = list(
            db.scalars(
                select(User)
                .where(
                    User.organization_id == organization_id,
                    User.role == UserRole.organization,
                    User.is_active.is_(True),
                )
                .order_by(User.id)
                .with_for_update()
            )
        )
        account = db.scalar(
            select(User)
            .where(
                User.deleted_at.is_(None),
                User.id == user_id,
                User.organization_id == organization_id,
                User.role == UserRole.organization,
            )
            .execution_options(populate_existing=True)
        )
        previous = account.is_active
        if previous and len(active_accounts) <= 1:
            raise api_error(
                409,
                "ACCOUNT_LAST_ACTIVE",
                "Add a successor before deactivating the last active personnel account.",
            )
    elif not previous and is_active:
        account = db.scalar(
            select(User)
            .where(
                User.deleted_at.is_(None),
                User.id == user_id,
                User.organization_id == organization_id,
                User.role == UserRole.organization,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        previous = account.is_active

    if previous != is_active:
        account.is_active = is_active
        db.flush()
        audit = record_audit(
            db,
            actor,
            "personnel.status_update",
            "account",
            account.id,
            {"username": account.username, "from": previous, "to": is_active},
        )
        audit.organization_id = organization_id
        audit.correlation_id = correlation_id
    return account


def reset_organization_user_password(
    db: Session,
    organization_id: int,
    user_id: int,
    password: str,
    actor: User,
    correlation_id: str | None = None,
) -> User:
    account = _organization_user_or_404(db, organization_id, user_id)
    account.password_hash = get_password_hash(password)
    db.flush()
    audit = record_audit(
        db,
        actor,
        "personnel.password_reset",
        "account",
        account.id,
        {"username": account.username},
    )
    audit.organization_id = organization_id
    audit.correlation_id = correlation_id
    return account


def delete_organization_user(db: Session, organization_id: int, user_id: int, actor: User) -> None:
    account = _organization_user_or_404(db, organization_id, user_id)
    if account.is_active:
        raise api_error(409, "ACCOUNT_STILL_ACTIVE", "Deactivate the account before deleting it.")
    account.deleted_at = datetime.now(timezone.utc)
    audit = record_audit(db, actor, "personnel.delete", "account", account.id,
                         {"username": account.username})
    audit.organization_id = organization_id
    db.flush()


def delete_organization(db: Session, organization_id: int, actor: User) -> None:
    organization = _organization_for_update(db, organization_id)
    if organization.status != RecordStatus.inactive:
        raise api_error(409, "ORG_STILL_ACTIVE", "Deactivate the organization before deleting it.")
    deleted_at = datetime.now(timezone.utc)
    organization.deleted_at = deleted_at
    accounts = list(db.scalars(select(User).where(
        User.organization_id == organization_id, User.deleted_at.is_(None)
    ).order_by(User.id).with_for_update()))
    for account in accounts:
        account.is_active = False
        account.deleted_at = deleted_at
    audit = record_audit(db, actor, "organization.delete", "organization", organization.id,
                         {"name": organization.name, "personnel_count": len(accounts)})
    audit.organization_id = organization_id
    db.flush()
