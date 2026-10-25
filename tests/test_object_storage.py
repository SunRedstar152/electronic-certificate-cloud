"""Storage contract checks use an in-memory private bucket, never a cloud account."""
from io import BytesIO
from pathlib import Path
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from config import settings
from database import get_db
from core.errors import register_error_handlers
from models import Certificate
from routers.certificates import router as owned_router
from routers.public import router as public_router
from services import object_storage as store
from services.storage_paths import resolve_certificate_file, StoredFileUnavailableError
from services.template_files import validate_template_image, store_template_image
from services.certificate_images import image_text
from services.certificate_qr import update_certificate_qr
from PIL import Image
from test_certificates_api import (organization, other_organization, organization_user, other_user,
    template, other_template, certificate_directory, authorization, certificate_payload)

@pytest.fixture
def bucket(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'STORAGE_BACKEND', 'supabase')
    monkeypatch.setattr(settings, 'TEMPLATE_DIR', tmp_path/'templates')
    monkeypatch.setattr(settings, 'FONT_DIR', tmp_path/'fonts')
    monkeypatch.setattr(settings, 'CERTIFICATE_DIR', tmp_path/'certificates')
    objects={}
    calls=[]
    def request(method, route, data=None, content_type=None):
        calls.append((method,route))
        if route.startswith('bucket/'): return BytesIO(b'{"public": false}')
        if method=='DELETE':
            for key in json.loads(data)['prefixes']: objects.pop(key,None)
            return BytesIO(b'{}')
        key=route.removeprefix('object/').removeprefix('authenticated/').split('/',1)[1]
        if method=='GET':
            if key not in objects:raise store.StorageObjectMissingError()
            return BytesIO(objects[key])
        if method=='POST' and key in objects:raise store.StorageUnavailableError('Conflict')
        objects[key]=data
        return BytesIO(b'{}')
    monkeypatch.setattr(store,'_request',request)
    return objects,calls

@pytest.fixture
def client(db_session):
    app=FastAPI();register_error_handlers(app)
    app.include_router(owned_router);app.include_router(public_router)
    app.dependency_overrides[get_db]=lambda:db_session
    return TestClient(app)


def test_revoked_deletion_removes_remote_object_without_downloading(client, db_session, organization_user, template, bucket):
    source = Path(template.background_image_path)
    destination = settings.TEMPLATE_DIR / 'background.png'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    template.background_image_path = str(destination)
    db_session.flush()
    client.headers.update(authorization(organization_user))
    response = client.post('/certificates', json=certificate_payload(template))
    assert response.status_code == 201
    code = response.json()['verification_code']
    ref = response.json()['image_path']
    path = store.reference_path(ref, settings.CERTIFICATE_DIR)
    path.unlink()
    objects, calls = bucket
    key = ref[len(store.PREFIX):]
    assert key in objects
    assert client.post(f'/certificates/{code}/revoke', json={'reason': 'Incorrect recipient'}).status_code == 200
    calls.clear()
    assert client.delete(f'/certificates/{code}').status_code == 204
    assert key not in objects
    assert not path.exists()
    assert not any(method == 'GET' for method, route in calls)
    assert db_session.scalar(select(Certificate).where(Certificate.verification_code == code)) is None


def test_strict_storage_cleanup_does_not_hide_remote_failure(bucket, monkeypatch):
    path = settings.CERTIFICATE_DIR / 'revoked.webp'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'cached image')
    def offline(*args, **kwargs):
        raise store.StorageUnavailableError('offline')
    monkeypatch.setattr(store, '_request', offline)
    with pytest.raises(store.StorageUnavailableError):
        store.discard_file(path, strict=True)
    assert path.exists()


def test_private_storage_survives_local_cache_loss_and_enforces_access(client,db_session,organization_user,other_user,template,bucket):
    source=Path(template.background_image_path)
    destination=settings.TEMPLATE_DIR/'background.png'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(source.read_bytes())
    template.background_image_path=str(destination)
    db_session.flush()
    response=client.post('/certificates',json=certificate_payload(template),headers={**authorization(organization_user),'Idempotency-Key':'remote-once'})
    assert response.status_code==201,response.text
    record=response.json();code=record['verification_code'];ref=record['image_path']
    assert ref.startswith('storage://certificates/')
    objects,calls=bucket
    assert ref.removeprefix('storage://') in objects
    path=resolve_certificate_file(ref);path.unlink()
    view=client.get(f'/api/public/certificates/{code}/image')
    assert view.status_code==200 and view.headers['content-type']=='image/webp'
    assert view.content==objects[ref.removeprefix('storage://')]
    png=client.get(f'/certificates/{code}/download',headers=authorization(organization_user))
    assert png.content.startswith(b'\x89PNG')
    with Image.open(BytesIO(png.content)) as a,Image.open(BytesIO(view.content)) as b:
        assert a.tobytes()==b.tobytes() and image_text(a)==image_text(b)
    assert client.get(f'/certificates/{code}/download',headers=authorization(other_user)).status_code==404
    duplicate=client.post('/certificates',json=certificate_payload(template),headers={**authorization(organization_user),'Idempotency-Key':'remote-once'})
    assert duplicate.json()['id']==record['id']
    assert len(objects)==1
    assert client.post(f'/certificates/{code}/revoke',json={'reason':'Check'},headers=authorization(organization_user)).status_code==200
    before=len(calls)
    assert client.get(f'/api/public/certificates/{code}/image').status_code==410
    assert len(calls)==before


def test_template_upload_publishes_webp_and_restores_after_cache_loss(bucket):
    data=BytesIO();Image.new('RGBA',(20,20),(40,20,50,80)).save(data,format='PNG')
    path=store_template_image(validate_template_image(data.getvalue(),'input.png'))
    ref=store.reference(path);path.unlink()
    from services.storage_paths import resolve_template_file
    restored=resolve_template_file(ref)
    with Image.open(restored) as image:assert image.format=='WEBP' and image.getpixel((0,0))==(40,20,50,80)


@pytest.mark.parametrize('reference',['storage://certificates/../secret','storage://certificates/%2e%2e/secret','storage://fonts/a.ttf','storage://certificates//a.webp'])
def test_unsafe_or_cross_category_reference_rejected(bucket,reference):
    with pytest.raises(StoredFileUnavailableError):resolve_certificate_file(reference)
    assert not bucket[1]


def test_private_bucket_required(bucket,monkeypatch):
    monkeypatch.setattr(store,'_request',lambda *a,**k:BytesIO(b'{"public":true}'))
    with pytest.raises(store.StorageUnavailableError):store.verify_storage()


def test_remote_failure_does_not_fall_back_to_stale_file(bucket,monkeypatch):
    import os
    path=settings.CERTIFICATE_DIR/'a.webp';path.parent.mkdir(parents=True);path.write_bytes(b'old');os.utime(path,(1,1))
    def fail(*a,**k):raise store.StorageUnavailableError('offline')
    monkeypatch.setattr(store,'_request',fail)
    with pytest.raises(store.StorageUnavailableError):resolve_certificate_file(store.reference(path))


def test_local_backend_can_read_portable_reference(bucket,monkeypatch):
    path=settings.CERTIFICATE_DIR/'moved.webp';path.parent.mkdir(parents=True);path.write_bytes(b'moved')
    monkeypatch.setattr(settings,'STORAGE_BACKEND','local')
    assert resolve_certificate_file('storage://certificates/moved.webp').read_bytes()==b'moved'
    assert not bucket[1]


def test_failed_upload_does_not_leave_local_template(bucket, monkeypatch):
    data=BytesIO();Image.new('RGB',(20,20),'white').save(data,format='PNG')
    def fail(*a, **k):raise store.StorageUnavailableError('offline')
    monkeypatch.setattr(store,'_request',fail)
    with pytest.raises(store.StorageUnavailableError):
        store_template_image(validate_template_image(data.getvalue(),'input.png'))
    assert not list(settings.TEMPLATE_DIR.glob('*.webp'))


def test_font_bytes_restore_after_cache_loss(bucket):
    from services.font_files import ValidatedFont, store_font
    font=ValidatedFont(data=b'validated-font-bytes',extension='.ttf',format='ttf',supports_thai=True,sha256='unused')
    path=store_font(font,42);ref=store.reference(path);path.unlink()
    assert store.materialize(ref,settings.FONT_DIR).read_bytes()==font.data


@pytest.mark.parametrize('key',['sb_secret_example','legacy-jwt'])
def test_request_uses_server_key_without_redirects(monkeypatch,key):
    monkeypatch.setattr(settings,'SUPABASE_SECRET_KEY',key)
    monkeypatch.setattr(settings,'SUPABASE_URL','https://example.supabase.co')
    captured={}
    class Opener:
        def open(self,request,timeout):
            captured['request']=request
            return BytesIO(b'{}')
    def opener(handler):
        assert isinstance(handler,store.NoRedirect)
        assert handler.redirect_request(None,None,None,None,None,None) is None
        return Opener()
    monkeypatch.setattr(store,'build_opener',opener)
    with store._request('GET','bucket/certificate-files') as response:assert response.read()==b'{}'
    request=captured['request']
    assert request.get_header('Apikey')==key
    assert request.get_header('Authorization')==(None if key.startswith('sb_secret_') else 'Bearer '+key)


def test_rollback_discards_remote_and_local_file(bucket):
    path=settings.CERTIFICATE_DIR/'rollback.webp'
    path.parent.mkdir(parents=True);path.write_bytes(b'encoded-image')
    store.publish_file(path)
    assert bucket[0]
    store.discard_file(path)
    assert not bucket[0] and not path.exists()
