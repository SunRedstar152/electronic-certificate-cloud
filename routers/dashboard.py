"""Bounded, organization-scoped reads for the dashboard."""
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.orm import Session
from auth import require_organization_user
from database import get_db
from models import User, Certificate, CertificateStatus, Batch, BatchStatus, Template, RecordStatus
from schemas import CertificateResponse

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

def batch_item(batch):
    return {key: getattr(batch,key) for key in (
        "id", "template_id", "topic", "issue_date", "source_type", "status",
        "total_count", "success_count", "failure_count", "created_at")}

@router.get("/summary")
def summary(db: Session=Depends(get_db), current_user: User=Depends(require_organization_user)):
    org=current_user.organization_id
    count,revoked=db.execute(select(func.count(Certificate.id),
        func.count(Certificate.id).filter(Certificate.status==CertificateStatus.revoked))
        .where(Certificate.organization_id==org)).one()
    return {
        "certificate_count":count,"revoked_count":revoked,
        "template_count":db.scalar(select(func.count(Template.id)).where(Template.organization_id==org, Template.status==RecordStatus.active, Template.deleted_at.is_(None))),
        "batch_count":db.scalar(select(func.count(Batch.id)).where(Batch.organization_id==org)),
        "recent_batches":[batch_item(b) for b in db.scalars(select(Batch).where(Batch.organization_id==org).order_by(Batch.id.desc()).limit(4))],
    }

def literal_pattern(search):
    return "%"+search.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"

@router.get("/certificates")
def certificates(offset: Annotated[int,Query(ge=0)]=0, limit: Annotated[int,Query(ge=1,le=100)]=20,
    search: Annotated[str,Query(max_length=255)]="", status: CertificateStatus|None=None,
    batch_id: Annotated[int|None,Query(gt=0)]=None,
    db: Session=Depends(get_db),current_user: User=Depends(require_organization_user)):
    filters=[Certificate.organization_id==current_user.organization_id]
    if status is not None: filters.append(Certificate.status==status)
    if batch_id is not None: filters.append(Certificate.batch_id==batch_id)
    if search.strip():
        pattern=literal_pattern(search)
        filters.append(or_(*[column.ilike(pattern,escape="\\") for column in (Certificate.recipient_name,Certificate.topic,Certificate.verification_code)]))
    total=db.scalar(select(func.count(Certificate.id)).where(*filters))
    rows=db.scalars(select(Certificate).where(*filters).order_by(Certificate.id).offset(offset).limit(limit))
    return {"total":total,"items":[CertificateResponse.model_validate(row) for row in rows]}

@router.get("/batches")
def batches(offset: Annotated[int,Query(ge=0)]=0, limit: Annotated[int,Query(ge=1,le=100)]=20,
    search: Annotated[str,Query(max_length=255)]="", status: BatchStatus|None=None,
    db: Session=Depends(get_db),current_user: User=Depends(require_organization_user)):
    filters=[Batch.organization_id==current_user.organization_id]
    if status is not None: filters.append(Batch.status==status)
    if search.strip():
        pattern=literal_pattern(search)
        filters.append(or_(Batch.topic.ilike(pattern,escape="\\"),cast(Batch.id,String).ilike(pattern,escape="\\")))
    total=db.scalar(select(func.count(Batch.id)).where(*filters))
    return {"total":total,"items":[batch_item(b) for b in db.scalars(select(Batch).where(*filters).order_by(Batch.id.desc()).offset(offset).limit(limit))]}
