from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from database import get_db
from main import app


def test_health_reports_database_readiness(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/healthz")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_returns_unavailable_without_exposing_database_error():
    class UnavailableDatabase:
        def execute(self, statement):
            raise OperationalError("SELECT 1", {}, RuntimeError("private-database-details"))

    app.dependency_overrides[get_db] = UnavailableDatabase
    try:
        response = TestClient(app).get("/healthz")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private-database-details" not in response.text
