"""Check real PostgreSQL isolation, including application error recovery."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from conftest import isolated_db_session, test_engine
from models import Organization


@pytest.mark.parametrize("action", ["commit", "rollback", "constraint_error"])
def test_application_transactions_cannot_escape_test_isolation(action):
    marker = f"isolation-{uuid4().hex[:16]}"
    query = select(func.count()).select_from(Organization).where(
        Organization.name.like(f"{marker}%")
    )

    with isolated_db_session() as session:
        original = Organization(name=marker, code=marker)
        session.add(original)
        session.commit()

        if action == "constraint_error":
            session.add(Organization(name=f"{marker}-duplicate", code=marker))
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        elif action == "rollback":
            session.add(Organization(name=f"{marker}-discard", code=f"{marker}-d"))
            session.flush()
            session.rollback()

        # Recovery can write and commit without committing the outer transaction.
        session.add(Organization(name=f"{marker}-after", code=f"{marker}-a"))
        session.commit()
        assert session.scalar(query) == 2
        with test_engine.connect() as observer:
            assert observer.scalar(query) == 0

    # Verify cleanup using a new connection after fixture teardown.
    with test_engine.connect() as observer:
        assert observer.scalar(query) == 0
