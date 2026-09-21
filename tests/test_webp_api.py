from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from database import get_db
from config import settings
from core.errors import register_error_handlers
from routers.certificates import router as owned_router
from routers.public import router as public_router
from services.certificate_images import image_text
from test_certificates_api import (organization,other_organization,organization_user,other_user,
    template,other_template,certificate_directory,authorization,certificate_payload,own_certificate)

@pytest.fixture
def client(db_session):
    app=FastAPI();register_error_handlers(app)
    app.include_router(owned_router);app.include_router(public_router)
    app.dependency_overrides[get_db]=lambda:db_session
    return TestClient(app)

def test_issue_webp_download_all_formats_and_revoke(client,organization_user,other_user,template,monkeypatch):
    monkeypatch.setattr(settings,"CERTIFICATE_STORAGE_FORMAT","webp")
    headers=authorization(organization_user)
    response=client.post('/certificates',json=certificate_payload(template),headers={**headers,'Idempotency-Key':'webp-once'})
    assert response.status_code==201,response.text
    record=response.json();code=record['verification_code'];path=Path(record['image_path'])
    assert path.suffix=='.webp'
    with Image.open(path) as stored:
        assert stored.format=='WEBP'
        pixels=stored.tobytes();metadata=image_text(stored)
    again=client.post('/certificates',json=certificate_payload(template),headers={**headers,'Idempotency-Key':'webp-once'})
    assert again.json()['id']==record['id']
    view=client.get(f'/api/public/certificates/{code}/image')
    assert view.headers['content-type']=='image/webp'
    assert view.content==path.read_bytes()
    for endpoint in (f'/certificates/{code}/download',f'/api/public/certificates/{code}/download?format=png'):
        exported=client.get(endpoint,headers=headers)
        assert exported.status_code==200
        assert exported.headers['content-type']=='image/png'
        with Image.open(BytesIO(exported.content)) as png:
            assert png.format=='PNG' and png.tobytes()==pixels
            assert image_text(png)==metadata
    for fmt,magic in [('jpg',b'\xff\xd8'),('pdf',b'%PDF')]:
        exported=client.get(f'/api/public/certificates/{code}/download?format={fmt}')
        assert exported.status_code==200 and exported.content.startswith(magic)
    assert client.get(f'/certificates/{code}/download',headers=authorization(other_user)).status_code==404
    assert client.post(f'/certificates/{code}/revoke',json={'reason':'Test revoke'},headers=headers).status_code==200
    for suffix in ('image','download?format=png','download?format=jpg','download?format=pdf'):
        assert client.get(f'/api/public/certificates/{code}/{suffix}').status_code==410

def test_existing_png_still_downloads_unchanged(client,own_certificate,organization_user):
    path=Path(own_certificate.image_path)
    result=client.get(f'/certificates/{own_certificate.verification_code}/download',headers=authorization(organization_user))
    assert result.status_code==200 and result.content==path.read_bytes()
