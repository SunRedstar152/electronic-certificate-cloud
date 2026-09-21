"""Refresh stored PNGs once at startup without blocking request handling."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import re
from threading import Event, Thread

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from config import settings
from models import Certificate
from services.certificate_qr import QrUpdateError, update_certificate_qr, verification_url
from services.storage_paths import StoredFileUnavailableError, resolve_certificate_file


logger = logging.getLogger(__name__)
# Separate short-lived connections keep maintenance timeouts out of API sessions.
# libpq's connect/query/TCP bounds let shutdown join the worker even if the DB
# becomes unavailable. This connects to the existing database, never creates one.
engine = create_engine(settings.DATABASE_URL, poolclass=NullPool, connect_args={
    "connect_timeout": 5,
    "options": "-csearch_path=public -c statement_timeout=5000 -c lock_timeout=3000",
    "keepalives": 1, "keepalives_idle": 5, "keepalives_interval": 2,
    "keepalives_count": 2, "tcp_user_timeout": 10000,
})
# Session lock shared by startup workers and the maintenance CLI.
_LOCK_KEYS = {"namespace": 1347113557, "job": 1364342096}


@dataclass
class QrMaintenanceReport:
    dry_run: bool
    status: str = "completed"
    counts: dict[str, int] = field(default_factory=lambda: {"updated": 0, "indexed": 0, "unchanged": 0, "failed": 0})
    failures: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _safe_reason(value: str) -> str:
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "qr_update_failed"


def run_qr_maintenance(*, dry_run: bool = False, stop_event: Event | None = None, batch_size: int = 25) -> QrMaintenanceReport:
    """Read certificate IDs/paths in bounded pages; modify image files only."""
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    stop = stop_event if stop_event is not None else Event()
    report = QrMaintenanceReport(dry_run=dry_run)
    base_url = str(settings.PUBLIC_BASE_URL)
    try:
        verification_url(base_url, "URLCHECK")
    except QrUpdateError:
        report.status, report.error = "failed", "invalid_base_url"
        return report
    if stop.is_set():
        report.status = "cancelled"
        return report

    try:
        with engine.connect() as connection:
            # The same physical connection must own the advisory lock throughout.
            # AUTOCOMMIT avoids an idle transaction for the duration of image work.
            connection = connection.execution_options(isolation_level="AUTOCOMMIT")
            locked = connection.scalar(text("SELECT pg_try_advisory_lock(:namespace, :job)"), _LOCK_KEYS)
            if not locked:
                report.status = "busy"
                return report
            try:
                high_water_id = connection.scalar(select(func.max(Certificate.id))) or 0
                last_id = 0
                while last_id < high_water_id and not stop.is_set():
                    rows = list(connection.execute(
                        select(Certificate.id, Certificate.verification_code, Certificate.image_path)
                        .where(Certificate.id > last_id, Certificate.id <= high_water_id)
                        .order_by(Certificate.id).limit(batch_size)
                    ))
                    if not rows:
                        break
                    for certificate_id, code, stored_path in rows:
                        if stop.is_set():
                            break
                        try:
                            path = resolve_certificate_file(stored_path or "")
                            status = update_certificate_qr(path, code, base_url, dry_run=dry_run, compress_level=settings.PNG_COMPRESS_LEVEL, webp_method=settings.WEBP_METHOD)
                            if not dry_run and status in {"updated", "indexed"}:
                                from services.object_storage import publish_file
                                try:
                                    publish_file(path, replace=True)
                                except Exception:
                                    if settings.STORAGE_BACKEND == "supabase":
                                        path.unlink(missing_ok=True)
                                    raise
                            report.counts[status] += 1
                        except StoredFileUnavailableError as exc:
                            reason = "image_path_unsafe" if exc.reason == "unsafe" else "image_missing"
                            _record_failure(report, certificate_id, reason)
                        except QrUpdateError as exc:
                            _record_failure(report, certificate_id, _safe_reason(exc.reason))
                        except Exception:
                            # Never log submitted values, paths or decoder messages.
                            _record_failure(report, certificate_id, "qr_update_failed")
                        last_id = certificate_id
                        if not dry_run:
                            stop.wait(0.02)
                if stop.is_set():
                    report.status = "cancelled"
                elif report.counts["failed"]:
                    report.status = "completed_with_errors"
            finally:
                try:
                    connection.scalar(text("SELECT pg_advisory_unlock(:namespace, :job)"), _LOCK_KEYS)
                except SQLAlchemyError:
                    # Discard this physical session rather than pool a held lock.
                    connection.invalidate()
    except SQLAlchemyError:
        report.status, report.error = "failed", "database_unavailable"
    logger.info("Certificate QR maintenance: status=%s dry_run=%s counts=%s", report.status, report.dry_run, report.counts)
    return report


def _record_failure(report: QrMaintenanceReport, certificate_id: int, reason: str) -> None:
    report.counts["failed"] += 1
    report.failures.append({"id": certificate_id, "reason": reason})
    logger.warning("Certificate QR update failed: id=%s reason=%s", certificate_id, reason)


@dataclass
class QrMaintenanceJob:
    stop: Event
    thread: Thread


def start_qr_maintenance() -> QrMaintenanceJob | None:
    if not settings.QR_UPDATE_ON_STARTUP:
        return None
    stop = Event()

    def work():
        try:
            while not stop.is_set():
                report = run_qr_maintenance(stop_event=stop)
                if report.status != "busy":
                    if report.error:
                        logger.warning("Certificate QR maintenance failed: %s", report.error)
                    return
                # A CLI dry-run may own the lock without updating any images.
                # Retry startup work after it exits; the CLI itself stays fast.
                if stop.wait(1):
                    return
        except Exception:
            logger.error("Certificate QR maintenance failed: unexpected_error")

    thread = Thread(target=work, name="certificate-qr-update", daemon=False)
    job = QrMaintenanceJob(stop, thread)
    thread.start()
    return job


def stop_qr_maintenance(job: QrMaintenanceJob | None) -> None:
    if job is not None:
        job.stop.set()
        job.thread.join()
