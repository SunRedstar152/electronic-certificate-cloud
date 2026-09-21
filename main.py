from routers import dashboard
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from config import settings
from core.errors import register_error_handlers
from database import get_db, SessionLocal
from routers import activities, admin, auth, batches, certificate_types, certificates, fonts, public, templates
from tasks import start_scheduler, stop_scheduler
from services.text_layout import validate_thai_shaping_configuration
from services.builtin_templates import ensure_all_original_templates
from services.qr_maintenance import start_qr_maintenance, stop_qr_maintenance


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_thai_shaping_configuration()
    from services.object_storage import verify_storage
    verify_storage()
    with SessionLocal() as db:
        ensure_all_original_templates(db)
        db.commit()
    scheduler_enabled = settings.ENABLE_SCHEDULER
    if scheduler_enabled:
        start_scheduler()
    qr_job = None
    try:
        qr_job = start_qr_maintenance()
        yield
    finally:
        await asyncio.to_thread(stop_qr_maintenance, qr_job)
        if scheduler_enabled:
            stop_scheduler()


class ProtectedStaticFiles(StaticFiles):
    _PRIVATE_ROOTS = {"uploads", "generated", "certificates"}

    async def get_response(self, path: str, scope):
        parts = PurePosixPath(path.replace("\\", "/")).parts
        if parts and parts[0].lower() in self._PRIVATE_ROOTS:
            return Response(status_code=404)
        return await super().get_response(path, scope)


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)
register_error_handlers(app)

app.mount("/static", ProtectedStaticFiles(directory=STATIC_DIR), name="static")
app.include_router(auth.router)
app.include_router(templates.router)
app.include_router(fonts.router)
app.include_router(certificates.router)
app.include_router(batches.router)
app.include_router(dashboard.router)
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(activities.router)
app.include_router(certificate_types.router)


@app.get("/healthz", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


def _page(filename: str) -> FileResponse:
    return FileResponse(
        STATIC_DIR / filename,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/", include_in_schema=False)
def public_page():
    return _page("index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return _page("login.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return _page("dashboard.html")


@app.get("/template-editor", include_in_schema=False)
def template_editor_page():
    return _page("dashboard.html")
