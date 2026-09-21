from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from database import get_db
from core.errors import register_error_handlers
from routers.dashboard import router
from test_certificates_api import (organization, other_organization, organization_user,
    other_user, admin_user, template, other_template, certificate_directory,
    own_certificate, other_certificate, authorization, stored_certificate)

@pytest.fixture
def client(db_session):
    app=FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_db]=lambda: db_session
    return TestClient(app)

def test_scope_summary_and_pagination(client,db_session,organization,organization_user,template,own_certificate,other_certificate):
    headers=authorization(organization_user)
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany): statements.append(statement)
    event.listen(db_session.bind,'before_cursor_execute',capture)
    try:
        result=client.get('/dashboard/summary',headers=headers)
        assert result.status_code==200
        assert result.json()['certificate_count']==1
        assert not any('batch_rows' in sql for sql in statements)
    finally: event.remove(db_session.bind,'before_cursor_execute',capture)
    for i in range(4):
        stored_certificate(db_session,organization,organization_user,template,f'EXTRA{i}','unused.png')
    first=client.get('/dashboard/certificates?limit=2',headers=headers).json()
    second=client.get('/dashboard/certificates?limit=2&offset=2',headers=headers).json()
    assert first['total']==second['total']==5
    assert len(first['items'])==len(second['items'])==2
    assert not ({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
    assert all(r['organization_id']==organization.id for r in first['items']+second['items'])
    assert client.get('/dashboard/certificates?search=EXTRA3',headers=headers).json()['total']==1
    assert client.get('/dashboard/certificates?search=%25',headers=headers).json()['total']==0
    assert client.get('/dashboard/certificates?limit=101',headers=headers).status_code==422

def test_access_and_empty_pages(client,admin_user,organization_user):
    assert client.get('/dashboard/summary').status_code==401
    assert client.get('/dashboard/summary',headers=authorization(admin_user)).status_code==403
    headers=authorization(organization_user)
    assert client.get('/dashboard/batches?offset=100',headers=headers).json()=={'total':0,'items':[]}
    assert client.get('/dashboard/certificates?status=invalid',headers=headers).status_code==422
