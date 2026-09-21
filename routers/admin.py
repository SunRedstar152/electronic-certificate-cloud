from datetime import datetime
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from auth import require_admin
from core.errors import api_error
from database import get_db
from models import (
    AuditLog,
    Batch,
    Certificate,
    Organization,
    RecordStatus,
    User,
    UserRole,
)
from schemas import (
    OrganizationCreate,
    OrganizationListItem,
    OrganizationResponse,
    OrganizationUpdate,
    PasswordResetRequest,
    PersonnelCreate,
    PersonnelStatusUpdate,
    UserResponse,
)
from services.organization_identity import normalize_organization_name
from services.accounts import (
    create_organization_account,
    create_organization_user,
    reset_organization_user_password,
    set_organization_user_status,
    update_organization as update_organization_service,
)


router = APIRouter(prefix="/admin", tags=["Admin"])


_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _page_size(value: int) -> int:
    return min(value, 100)


def _organization_json(organization: Organization) -> dict:
    return OrganizationResponse.model_validate(organization).model_dump(mode="json")


def _account_json(account: User | None) -> dict | None:
    if account is None:
        return None
    return UserResponse.model_validate(account).model_dump(mode="json")


def _organization_account_json(
    organization: Organization, account: User | None
) -> dict:
    return {
        "organization": _organization_json(organization),
        "account": _account_json(account),
    }


def _organization_list_item_json(
    organization: Organization,
    personnel_count: int,
    active_personnel_count: int,
) -> dict:
    return OrganizationListItem(
        organization=OrganizationResponse.model_validate(organization),
        personnel_count=personnel_count,
        active_personnel_count=active_personnel_count,
    ).model_dump(mode="json")


def _request_correlation_id(request: Request) -> str:
    supplied = request.headers.get("X-Correlation-ID")
    if supplied and _CORRELATION_ID_PATTERN.fullmatch(supplied):
        return supplied
    return str(uuid4())


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    data: OrganizationCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    organization, account = create_organization_account(db, data, current_admin)
    db.commit()
    db.refresh(organization)
    db.refresh(account)
    return _organization_account_json(organization, account)


@router.get("/organizations")
def list_organizations(
    search: str | None = Query(default=None, max_length=255),
    status_filter: RecordStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    db: Session = Depends(get_db),
    _current_admin: User = Depends(require_admin),
):
    conditions = []
    normalized_search = normalize_organization_name(search) if search else ""
    if normalized_search:
        pattern = f"%{normalized_search}%"
        username_match = (
            select(User.id)
            .where(
                User.organization_id == Organization.id,
                User.role == UserRole.organization,
                User.username.ilike(pattern),
            )
            .correlate(Organization)
            .exists()
        )
        conditions.append(
            or_(
                Organization.name.ilike(pattern),
                username_match,
            )
        )
    if status_filter is not None:
        conditions.append(Organization.status == status_filter)

    size = _page_size(page_size)
    personnel_count = (
        select(func.count(User.id))
        .where(
            User.organization_id == Organization.id,
            User.role == UserRole.organization,
        )
        .correlate(Organization)
        .scalar_subquery()
    )
    active_personnel_count = (
        select(func.count(User.id))
        .where(
            User.organization_id == Organization.id,
            User.role == UserRole.organization,
            User.is_active.is_(True),
        )
        .correlate(Organization)
        .scalar_subquery()
    )
    base = select(Organization, personnel_count, active_personnel_count)
    count_stmt = select(func.count()).select_from(Organization).where(*conditions)
    rows = db.execute(
        base.where(*conditions)
        .order_by(func.lower(Organization.name), Organization.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {
        "items": [
            _organization_list_item_json(
                organization, int(personnel_count), int(active_personnel_count)
            )
            for organization, personnel_count, active_personnel_count in rows
        ],
        "page": page,
        "page_size": size,
        "total": db.scalar(count_stmt) or 0,
    }


@router.patch("/organizations/{organization_id}")
def update_organization(
    organization_id: int,
    data: OrganizationUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    organization = update_organization_service(db, organization_id, data, current_admin)
    db.commit()
    db.refresh(organization)
    return {"organization": _organization_json(organization)}


@router.get("/organizations/{organization_id}/users")
def list_organization_users(
    organization_id: int,
    db: Session = Depends(get_db),
    _current_admin: User = Depends(require_admin),
):
    if db.get(Organization, organization_id) is None:
        raise api_error(404, "ORG_NOT_FOUND", "Organization not found.")
    items = list(
        db.scalars(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.role == UserRole.organization,
            )
            .order_by(func.lower(User.username), User.id)
        )
    )
    return {"items": [_account_json(item) for item in items], "total": len(items)}


@router.post("/organizations/{organization_id}/users", status_code=status.HTTP_201_CREATED)
def create_organization_user_route(
    organization_id: int,
    data: PersonnelCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    account = create_organization_user(
        db,
        organization_id,
        data,
        current_admin,
        _request_correlation_id(request),
    )
    db.commit()
    db.refresh(account)
    return {"account": _account_json(account)}


@router.patch("/organizations/{organization_id}/users/{user_id}")
def update_organization_user_route(
    organization_id: int,
    user_id: int,
    data: PersonnelStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    account = set_organization_user_status(
        db,
        organization_id,
        user_id,
        data.is_active,
        current_admin,
        _request_correlation_id(request),
    )
    db.commit()
    db.refresh(account)
    return {"account": _account_json(account)}


@router.post("/organizations/{organization_id}/users/{user_id}/reset-password")
def reset_organization_user_password_route(
    organization_id: int,
    user_id: int,
    data: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    account = reset_organization_user_password(
        db,
        organization_id,
        user_id,
        data.password,
        current_admin,
        _request_correlation_id(request),
    )
    db.commit()
    db.refresh(account)
    return {"account": _account_json(account)}


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    _current_admin: User = Depends(require_admin),
):
    organization_counts = dict(
        db.execute(
            select(Organization.status, func.count(Organization.id)).group_by(
                Organization.status
            )
        ).all()
    )
    account_counts = dict(
        db.execute(
            select(User.is_active, func.count(User.id))
            .where(User.role == UserRole.organization)
            .group_by(User.is_active)
        ).all()
    )
    active_organizations = organization_counts.get(RecordStatus.active, 0)
    inactive_organizations = organization_counts.get(RecordStatus.inactive, 0)
    active_accounts = account_counts.get(True, 0)
    inactive_accounts = account_counts.get(False, 0)
    return {
        "organizations": {
            "total": active_organizations + inactive_organizations,
            "active": active_organizations,
            "inactive": inactive_organizations,
        },
        "accounts": {
            "total": active_accounts + inactive_accounts,
            "active": active_accounts,
            "inactive": inactive_accounts,
        },
        "batches": {
            "total": db.scalar(select(func.count()).select_from(Batch)) or 0
        },
        "certificates": {
            "total": db.scalar(select(func.count()).select_from(Certificate)) or 0
        },
    }


def _audit_details_json(audit: AuditLog) -> dict:
    if audit.action != "organization.create":
        return audit.details
    return {key: value for key, value in audit.details.items() if key != "code"}


def _audit_json(audit: AuditLog) -> dict:
    return {
        "id": audit.id,
        "action": audit.action,
        "target_type": audit.target_type,
        "target_id": audit.target_id,
        "correlation_id": audit.correlation_id,
        "details": _audit_details_json(audit),
        "created_at": audit.created_at,
        "actor": _account_json(audit.actor),
        "organization": (
            _organization_json(audit.organization)
            if audit.organization is not None
            else None
        ),
    }


@router.get("/audit-logs")
def list_audit_logs(
    organization_id: int | None = Query(default=None, ge=1),
    action: str | None = Query(default=None, max_length=100),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    db: Session = Depends(get_db),
    _current_admin: User = Depends(require_admin),
):
    conditions = []
    if organization_id is not None:
        conditions.append(AuditLog.organization_id == organization_id)
    if action:
        conditions.append(AuditLog.action == action)
    if date_from is not None:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.created_at <= date_to)

    size = _page_size(page_size)
    items = list(
        db.scalars(
            select(AuditLog)
            .options(joinedload(AuditLog.actor), joinedload(AuditLog.organization))
            .where(*conditions)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    total = db.scalar(
        select(func.count()).select_from(AuditLog).where(*conditions)
    )
    return {
        "items": [_audit_json(item) for item in items],
        "page": page,
        "page_size": size,
        "total": total or 0,
    }
