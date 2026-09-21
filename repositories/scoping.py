from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from models import User, UserRole


def owned_query(model: type[Any], current_user: User, db: Session) -> Select:
    del db
    statement = select(model)
    if current_user.role == UserRole.admin:
        return statement
    return statement.where(model.organization_id == current_user.organization_id)
