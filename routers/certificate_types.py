from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import require_organization_user
from core.errors import api_error
from database import get_db
from models import CertificateType, User
from schemas.certificate_types import CertificateTypeCreate, CertificateTypeResponse
from services.audit import record_audit


router = APIRouter(prefix="/certificate-types", tags=["certificate-types"])


def _owned(db: Session, type_id: int, user: User) -> CertificateType:
    value = db.scalar(select(CertificateType).where(CertificateType.id == type_id, CertificateType.organization_id == user.organization_id))
    if value is None:
        raise api_error(404, "CERTIFICATE_TYPE_NOT_FOUND", "The certificate type was not found.")
    return value


@router.get("", response_model=list[CertificateTypeResponse])
def list_certificate_types(db: Session = Depends(get_db), current_user: User = Depends(require_organization_user)):
    return list(db.scalars(select(CertificateType).where(CertificateType.organization_id == current_user.organization_id).order_by(CertificateType.name)))


@router.post("", response_model=CertificateTypeResponse, status_code=status.HTTP_201_CREATED)
def create_certificate_type(data: CertificateTypeCreate, db: Session = Depends(get_db), current_user: User = Depends(require_organization_user)):
    value = CertificateType(organization_id=current_user.organization_id, **data.model_dump())
    db.add(value)
    db.flush()
    record_audit(db, current_user, "certificate_type.create", "certificate_type", value.id, {"name": value.name})
    db.commit()
    db.refresh(value)
    return value


@router.patch("/{type_id}", response_model=CertificateTypeResponse)
def update_certificate_type(type_id: int, data: CertificateTypeCreate, db: Session = Depends(get_db), current_user: User = Depends(require_organization_user)):
    value = _owned(db, type_id, current_user)
    for key, item in data.model_dump().items():
        setattr(value, key, item)
    record_audit(db, current_user, "certificate_type.update", "certificate_type", value.id, {"name": value.name})
    db.commit()
    db.refresh(value)
    return value
