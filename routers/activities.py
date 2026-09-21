from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin, require_organization_user
from core.errors import api_error
from database import get_db
from models import Activity, ActivityApprovalLog, ActivityStatus, User, UserRole
from schemas.activities import ActivityCreate, ActivityDecision, ActivityResponse
from services.audit import record_audit


router = APIRouter(prefix="/activities", tags=["activities"])


def _get_activity(db: Session, activity_id: int, user: User) -> Activity:
    query = select(Activity).where(Activity.id == activity_id)
    if user.role != UserRole.admin:
        query = query.where(Activity.organization_id == user.organization_id)
    activity = db.scalar(query)
    if activity is None:
        raise api_error(404, "ACTIVITY_NOT_FOUND", "The activity was not found.")
    return activity


@router.post("", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
def create_activity(data: ActivityCreate, db: Session = Depends(get_db), current_user: User = Depends(require_organization_user)):
    activity = Activity(
        organization_id=current_user.organization_id,
        department_id=current_user.organization_id,
        proposed_by_id=current_user.id,
        approval_status=ActivityStatus.DRAFT,
        **data.model_dump(),
    )
    db.add(activity)
    db.flush()
    record_audit(db, current_user, "activity.create", "activity", activity.id, {"activity_name": activity.activity_name})
    db.commit()
    db.refresh(activity)
    return activity


@router.get("", response_model=list[ActivityResponse])
def list_activities(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(Activity).order_by(Activity.created_at.desc(), Activity.id.desc())
    if current_user.role != UserRole.admin:
        query = query.where(Activity.organization_id == current_user.organization_id)
    return list(db.scalars(query))


@router.get("/{activity_id}", response_model=ActivityResponse)
def get_activity(activity_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _get_activity(db, activity_id, current_user)


@router.post("/{activity_id}/submit", response_model=ActivityResponse)
def submit_activity(activity_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_organization_user)):
    activity = _get_activity(db, activity_id, current_user)
    if activity.approval_status is not ActivityStatus.DRAFT:
        raise api_error(409, "ACTIVITY_INVALID_TRANSITION", "Only draft activities can be submitted.")
    activity.approval_status = ActivityStatus.PENDING_APPROVAL
    record_audit(db, current_user, "activity.submit", "activity", activity.id, {})
    db.commit()
    db.refresh(activity)
    return activity


def _decide(activity_id: int, decision: ActivityStatus, data: ActivityDecision, db: Session, current_user: User) -> Activity:
    activity = _get_activity(db, activity_id, current_user)
    if activity.approval_status is not ActivityStatus.PENDING_APPROVAL:
        raise api_error(409, "ACTIVITY_INVALID_TRANSITION", "Only pending activities can be decided.")
    activity.approval_status = decision
    activity.approved_by_id = current_user.id if decision is ActivityStatus.APPROVED else None
    activity.approved_at = datetime.now(timezone.utc) if decision is ActivityStatus.APPROVED else None
    activity.approval_comment = data.comment
    db.add(ActivityApprovalLog(activity_id=activity.id, organization_id=activity.organization_id, decision=decision, decided_by_id=current_user.id, comment=data.comment))
    record_audit(db, current_user, f"activity.{decision.value.lower()}", "activity", activity.id, {"comment": data.comment} if data.comment else {})
    db.commit()
    db.refresh(activity)
    return activity


@router.post("/{activity_id}/approve", response_model=ActivityResponse)
def approve_activity(activity_id: int, data: ActivityDecision | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in {UserRole.admin, UserRole.organization}:
        raise api_error(403, "AUTH_FORBIDDEN", "You do not have permission to perform this action.")
    return _decide(activity_id, ActivityStatus.APPROVED, data or ActivityDecision(), db, current_user)


@router.post("/{activity_id}/reject", response_model=ActivityResponse)
def reject_activity(activity_id: int, data: ActivityDecision | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in {UserRole.admin, UserRole.organization}:
        raise api_error(403, "AUTH_FORBIDDEN", "You do not have permission to perform this action.")
    return _decide(activity_id, ActivityStatus.REJECTED, data or ActivityDecision(), db, current_user)
