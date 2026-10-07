import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    raise pytest.UsageError("TEST_DATABASE_URL must be set for the test database.")

if not TEST_DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")):
    raise pytest.UsageError("TEST_DATABASE_URL must use PostgreSQL.")


os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def close_database_pools():
    yield
    # Close pooled DBAPI connections before Python's shutdown/garbage collection.
    from database import engine

    engine.dispose()
    test_engine.dispose()


@pytest.fixture(autouse=True)
def isolated_template_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMPLATE_DIR", tmp_path)


@contextmanager
def isolated_db_session():
    connection = test_engine.connect()
    transaction = connection.begin()
    # Application commit/rollback must affect a SAVEPOINT, not the fixture's
    # outer transaction. Otherwise rollback ends isolation for later writes.
    session = TestSessionLocal(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        try:
            session.close()
            assert transaction.is_active, "Test ended the outer isolation transaction"
            transaction.rollback()
        finally:
            connection.close()


@pytest.fixture
def db_session():
    with isolated_db_session() as session:
        yield session
