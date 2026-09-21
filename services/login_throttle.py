"""PostgreSQL login throttling shared by every worker and application restart."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config import settings
from core.errors import api_error
from models import LoginAttemptBucket


def bucket_keys(username: str, client_ip: str, secret_key: str) -> tuple[str, str]:
    """Domain-separated keys preserve exact, case-sensitive username identity."""
    secret = secret_key.encode("utf-8")

    def digest(parts: list[str]) -> str:
        payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
        return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    return (
        digest(["login-ip-v1", client_ip]),
        digest(["login-pair-v1", client_ip, username]),
    )


def retry_after(bucket: LoginAttemptBucket, maximum: int, now: datetime) -> int:
    if bucket.failed_attempts < maximum or bucket.expires_at <= now:
        return 0
    return math.ceil((bucket.expires_at - now).total_seconds())


def rate_limit_error(seconds: int):
    error = api_error(
        429,
        "AUTH_RATE_LIMITED",
        "เข้าสู่ระบบไม่สำเร็จหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่",
    )
    error.headers = {"Retry-After": str(seconds)}
    return error


@dataclass
class LoginAttempt:
    ip_bucket: LoginAttemptBucket
    pair_bucket: LoginAttemptBucket
    now: datetime
    window_seconds: int

    def record_failure(self) -> None:
        for bucket in (self.ip_bucket, self.pair_bucket):
            if bucket.expires_at <= self.now:
                bucket.failed_attempts = 0
                bucket.expires_at = self.now + timedelta(seconds=self.window_seconds)
            bucket.failed_attempts += 1

    def record_success(self) -> None:
        # An attacker with one valid account must not replenish the IP budget.
        self.pair_bucket.failed_attempts = 0
        self.pair_bucket.expires_at = self.now


def _lock_bucket(db: Session, key: str) -> LoginAttemptBucket:
    # The conflict UPDATE acquires a row lock even when the bucket exists.
    # Unlike INSERT DO NOTHING followed by SELECT, there is no deletion gap
    # where concurrent expiry cleanup could remove the bucket before it locks.
    statement = (
        insert(LoginAttemptBucket)
        .values(bucket_key=key, failed_attempts=0, expires_at=func.clock_timestamp())
        .on_conflict_do_update(
            index_elements=[LoginAttemptBucket.bucket_key],
            set_={"bucket_key": key},
        )
        .returning(LoginAttemptBucket)
    )
    return db.scalars(statement, execution_options={"populate_existing": True}).one()


def _cleanup_expired(db: Session, now: datetime, active_keys: list[str]) -> None:
    # A bound timestamp keeps this predicate indexable. SKIP LOCKED avoids
    # waiting on another login; LIMIT bounds work on each request.
    expired = (
        select(LoginAttemptBucket.bucket_key)
        .where(
            LoginAttemptBucket.expires_at <= now,
            LoginAttemptBucket.bucket_key.not_in(active_keys),
        )
        .order_by(LoginAttemptBucket.expires_at, LoginAttemptBucket.bucket_key)
        .limit(settings.LOGIN_CLEANUP_BATCH_SIZE)
        .with_for_update(skip_locked=True)
        .cte("expired_login_buckets")
    )
    db.execute(
        delete(LoginAttemptBucket)
        .where(LoginAttemptBucket.bucket_key.in_(select(expired.c.bucket_key)))
        .execution_options(synchronize_session=False)
    )


@contextmanager
def login_attempt(db: Session, username: str, client_ip: str) -> Iterator[LoginAttempt]:
    """Commit counters before the route raises 401; retain locks through hashing.

    Every request acquires IP then pair in the same order. The IP lock serializes
    guesses for all usernames from that address, across workers. A limited IP
    does not create more pair rows for attacker-supplied usernames. This context
    owns the route's transaction; unexpected errors roll it back and fail closed.
    """
    ip_key, pair_key = bucket_keys(username, client_ip, settings.SECRET_KEY)
    active_keys = [ip_key]
    limited_seconds = 0
    try:
        ip_bucket = _lock_bucket(db, ip_key)
        # PostgreSQL's transaction timestamp can predate a lock wait. Read the
        # wall clock after acquiring locks so expired windows open immediately.
        now = db.scalar(select(func.clock_timestamp()))
        limited_seconds = retry_after(ip_bucket, settings.LOGIN_IP_MAX_FAILURES, now)
        if not limited_seconds:
            pair_bucket = _lock_bucket(db, pair_key)
            active_keys.append(pair_key)
            now = db.scalar(select(func.clock_timestamp()))
            limited_seconds = retry_after(pair_bucket, settings.LOGIN_PAIR_MAX_FAILURES, now)
            if not limited_seconds:
                yield LoginAttempt(ip_bucket, pair_bucket, now, settings.LOGIN_WINDOW_SECONDS)

        # Cleanup must run after acquisition and use SKIP LOCKED, so it cannot
        # reverse the IP/pair lock order and deadlock simultaneous logins.
        _cleanup_expired(db, now, active_keys)
        db.commit()
    except Exception:
        db.rollback()
        raise

    if limited_seconds:
        raise rate_limit_error(limited_seconds)
