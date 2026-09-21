# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REQUIRE_RAQM=true \
    ENABLE_SCHEDULER=false \
    TEMPLATE_DIR=/app/storage/uploads/templates \
    CERTIFICATE_DIR=/app/storage/generated/certificates \
    FONT_DIR=/app/storage/uploads/fonts

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libraqm0 libfribidi0 libharfbuzz0b ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p storage/uploads/templates storage/uploads/fonts storage/generated/certificates \
    && chown -R app:app storage

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "from PIL import features; assert features.check('raqm'), 'Pillow requires RAQM for Thai text'; assert features.check('webp'), 'Pillow requires lossless WebP support'"

COPY auth.py config.py database.py main.py models.py tasks.py alembic.ini ./
COPY core/ ./core/
COPY repositories/ ./repositories/
COPY routers/ ./routers/
COPY schemas/ ./schemas/
COPY services/ ./services/
COPY alembic/ ./alembic/
COPY sql/ ./sql/
COPY assets/ ./assets/
COPY static/ ./static/
COPY scripts/start_cloud.py scripts/container_entrypoint.py scripts/create_admin.py scripts/run_scheduler.py scripts/initialize_database.py scripts/update_certificate_qr.py ./scripts/

USER app:app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).close()"
ENTRYPOINT ["python", "scripts/container_entrypoint.py"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
