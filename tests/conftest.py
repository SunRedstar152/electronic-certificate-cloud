import os

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


@pytest.fixture(autouse=True)
def isolated_template_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMPLATE_DIR", tmp_path)


@pytest.fixture
def db_session():
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
