from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from models import AuditLog, User


_SENSITIVE_KEYS = {"password", "token", "secret"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def record_audit(
    db: Session,
    actor: User,
    action: str,
    target_type: str,
    target_id: object | None,
    details: dict[str, Any],
) -> AuditLog:
    safe_details = _scrub(jsonable_encoder(details))
    audit = AuditLog(
        actor_id=actor.id,
        organization_id=actor.organization_id,
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        details=safe_details,
    )
    db.add(audit)
    return audit
