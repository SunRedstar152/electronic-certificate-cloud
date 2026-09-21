"""Scheduled maintenance tasks for the application."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal, engine
from config import settings
from models import AuditLog


logger = logging.getLogger(__name__)
_AUDIT_CLEANUP_JOB_ID = "delete_old_audit_logs"

jobstores = {"default": SQLAlchemyJobStore(engine=engine)}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone=timezone.utc)


def audit_retention_days() -> int:
    """Return the validated audit retention period from application settings."""
    return settings.AUDIT_RETENTION_DAYS


def delete_old_audit_logs() -> None:
    """Remove audit records outside the configured retention period."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=audit_retention_days())
        deleted_count = (
            db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
        )
        db.commit()
        logger.info("Audit retention cleanup removed %s audit records.", deleted_count)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error("Audit retention cleanup failed.")
    finally:
        db.close()


def start_scheduler() -> None:
    """Register the daily cleanup and start the local scheduler once."""
    scheduler.add_job(
        delete_old_audit_logs,
        "interval",
        days=1,
        id=_AUDIT_CLEANUP_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    """Stop the scheduler only when it is currently running."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
