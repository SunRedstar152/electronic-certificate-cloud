"""PostgreSQL 18 + storage backups. Uses Python's standard library and PG clients."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from zoneinfo import ZoneInfo


GENERATOR = "pkru-certificate-backup"
SET_NAME = re.compile(r"auto-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
ASSETS = {"templates": "background_image_path", "font_assets": "file_path", "certificates": "image_path"}
DATABASE = "pkru_certificate"


class BackupBusy(RuntimeError):
    """Another process owns the backup lock and its shared status."""


def utcnow():
    return datetime.now(timezone.utc)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def asset_member(value):
    if value == "builtin:original":
        return None  # Shipped in the application image, not mutable storage.
    normalized = value.replace("\\", "/")
    if normalized.startswith("/app/"):
        normalized = normalized[len("/app/"):]
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "storage":
        raise ValueError("Asset reference is outside managed storage; migrate its path before backup")
    return path.as_posix()


def archive_storage(storage, destination):
    storage = Path(storage)
    if not storage.is_dir() or storage.is_symlink():
        raise ValueError("Storage directory is missing or unsafe")
    inventory = {}
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(storage, arcname="storage", recursive=False)
        for path in sorted(storage.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Unsafe file in storage")
            member = "storage/" + path.relative_to(storage).as_posix()
            if path.is_file():
                inventory[member] = {"size": path.stat().st_size, "sha256": sha256(path)}
            archive.add(path, arcname=member, recursive=False)
    return inventory


def verify_integrity(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if (manifest.get("format_version") != 1 or manifest.get("generator") != GENERATOR
            or manifest.get("database") != DATABASE or manifest.get("schemas") != ["public"]):
        raise ValueError("Unsupported backup manifest")
    for name in ("database.dump", "storage.tar.gz"):
        if sha256(directory / name) != manifest["checksums"].get(name):
            raise ValueError("Backup checksum mismatch: " + name)
    actual = {}
    seen = set()
    with tarfile.open(directory / "storage.tar.gz", "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or not path.parts or path.parts[0] != "storage"
                    or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError("Unsafe storage archive member")
            seen.add(member.name)
            if member.isfile():
                with archive.extractfile(member) as stream:
                    actual[member.name] = {"size": member.size, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
    if actual != manifest["storage_files"]:
        raise ValueError("Storage file inventory/checksum mismatch")
    for reference in manifest["asset_references"]:
        member = asset_member(reference["path"])
        if member is not None and member not in actual:
            raise ValueError("Missing referenced storage asset")
    return manifest


def generated_sets(root):
    return sorted((path for path in Path(root).iterdir()
                   if SET_NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink()), reverse=True)


def apply_retention(root, retain):
    if retain < 1:
        raise ValueError("BACKUP_RETAIN_COUNT must be at least 1")
    valid = []
    for path in generated_sets(root):
        try:
            verify_integrity(path)
            valid.append(path)
        except (ValueError, KeyError, OSError, tarfile.TarError):
            continue  # Never remove manual, partial, unknown, or damaged sets.
    for path in valid[retain:]:
        remove_owned_directory(root, path)


def remove_owned_directory(root, path):
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("Refusing cleanup outside backup root")
    shutil.rmtree(path)


def backup_due(last_success, now, zone, daily_time):
    hour, minute = map(int, daily_time.split(":"))
    local = now.astimezone(zone)
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if last_success is None or now - last_success >= timedelta(hours=24):
        return True
    return last_success < scheduled <= now


def run_command(arguments, timeout=3600):
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if result.returncode:
        # Never print a subprocess environment, connection string, or SQL/data.
        raise RuntimeError(Path(arguments[0]).name + " failed; check database availability and backup permissions")
    return result.stdout


class PgSession:
    """Keep advisory/table locks and an exported snapshot alive in one psql session."""
    def __enter__(self):
        self.process = subprocess.Popen(
            ["psql", "-X", "-q", "-A", "-t", "-w", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()
        return self

    def _read_output(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def query(self, sql):
        marker = "backup_end_" + uuid.uuid4().hex
        self.process.stdin.write(sql + ";\n\\echo " + marker + "\n")
        self.process.stdin.flush()
        lines = []
        deadline = time.monotonic() + 150
        while True:
            try:
                line = self.lines.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                self.process.kill()
                raise RuntimeError("psql query timed out after 150 seconds") from None
            if not line:
                raise RuntimeError("psql failed; check database availability, schema, and lock contention")
            if line.rstrip("\r\n") == marker:
                return "\n".join(lines).strip()
            lines.append(line.rstrip("\r\n"))

    def __exit__(self, *unused):
        try:
            if self.process.poll() is None:
                self.process.stdin.write("\\q\n")
                self.process.stdin.flush()
                self.process.wait(timeout=10)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        finally:
            self.process.stdin.close()
            self.reader.join(timeout=10)
            self.process.stdout.close()


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def tables(session):
    return json.loads(session.query("SELECT COALESCE(json_agg(tablename ORDER BY tablename), '[]') FROM pg_tables WHERE schemaname = 'public'"))


def validate_database(session):
    if session.query("SELECT current_database()") != DATABASE:
        raise ValueError("Backup/verification database must be pkru_certificate")
    version = int(session.query("SHOW server_version_num"))
    if not 180000 <= version < 190000:
        raise ValueError("PostgreSQL 18 is required")
    return version


def database_metadata(session):
    version = validate_database(session)
    schemas = json.loads(session.query("SELECT json_agg(nspname ORDER BY nspname) FROM pg_namespace WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%'"))
    if schemas != ["public"]:
        raise ValueError("Application database must use only the public schema")
    names = tables(session)
    if not ASSETS.keys() <= set(names):
        raise ValueError("Application asset tables are missing; run migrations first")
    counts = {name: int(session.query("SELECT count(*) FROM public." + quote_identifier(name))) for name in names}
    references = []
    for table, column in ASSETS.items():
        rows = json.loads(session.query(
            "SELECT COALESCE(json_agg(r ORDER BY id), '[]') FROM (SELECT id, "
            + quote_identifier(column) + " AS path FROM public." + quote_identifier(table)
            + " WHERE " + quote_identifier(column) + " IS NOT NULL AND " + quote_identifier(column) + " <> '') r"
        ))
        references.extend({"table": table, **row} for row in rows)
    return {"database": DATABASE, "schemas": schemas, "server_version_num": version,
            "table_counts": counts, "asset_references": references}


def create_backup(root, storage, retain):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    started = utcnow()
    name = "auto-" + started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    staging = root / ("." + name + ".partial")
    with PgSession() as session:
        if session.query("SELECT pg_try_advisory_lock(731904286113)") != "t":
            raise BackupBusy("Another backup is running; try again after it finishes")
        write_json(root / "status.json", {"state": "running", "last_attempt_at": utcnow().isoformat()})
        names = tables(session)
        if not names:
            raise ValueError("No application tables found")
        staging.mkdir()
        try:
            session.query("BEGIN ISOLATION LEVEL REPEATABLE READ")
            session.query("SET LOCAL lock_timeout = '30s'")
            session.query("SET LOCAL statement_timeout = '120s'")
            # Acquire locks before the first snapshot-producing SELECT. Existing
            # writers finish first; subsequent writes wait until capture ends.
            session.query("LOCK TABLE " + ", ".join("public." + quote_identifier(name) for name in names) + " IN SHARE MODE")
            snapshot = session.query("SELECT pg_export_snapshot()")
            metadata = database_metadata(session)
            run_command(["pg_dump", "-w", "--format=custom", "--schema=public", "--no-owner", "--no-privileges",
                         "--lock-wait-timeout=30s", "--snapshot=" + snapshot, "--file=" + str(staging / "database.dump")])
            inventory = archive_storage(storage, staging / "storage.tar.gz")
            session.query("COMMIT")
            run_command(["pg_restore", "--list", str(staging / "database.dump")], timeout=120)
            manifest = {"format_version": 1, "generator": GENERATOR, "started_at": started.isoformat(),
                        "completed_at": utcnow().isoformat(), **metadata, "storage_files": inventory,
                        "checksums": {file: sha256(staging / file) for file in ("database.dump", "storage.tar.gz")}}
            write_json(staging / "manifest.json", manifest)
            verify_integrity(staging)
            staging.replace(root / name)
            apply_retention(root, retain)
            write_json(root / "status.json", {"state": "ready", "last_attempt_at": utcnow().isoformat()})
            return name
        finally:
            if staging.exists():
                remove_owned_directory(root, staging)


def verify_restored(directory):
    """Called ONLY by the host helper against its isolated temporary container."""
    if os.environ.get("BACKUP_DISPOSABLE_VERIFY") != "1" or os.environ.get("PGHOST") != "127.0.0.1":
        raise ValueError("Restoration verification requires the disposable host-helper environment")
    manifest = verify_integrity(directory)
    with PgSession() as session:
        # Check the same database that pg_restore will target before --clean.
        validate_database(session)
        if tables(session):
            raise ValueError("Refusing restoration into a nonempty database")
    run_command(["pg_restore", "-w", "--dbname=" + DATABASE, "--clean", "--if-exists", "--no-owner", "--no-privileges", "--exit-on-error",
                 str(Path(directory) / "database.dump")])
    with PgSession() as session:
        actual = database_metadata(session)
    for key in ("database", "schemas", "table_counts", "asset_references"):
        if actual[key] != manifest[key]:
            raise ValueError("Restored database differs from backup: " + key)
    with tempfile.TemporaryDirectory(prefix="pkru-restore-") as temporary:
        with tarfile.open(Path(directory) / "storage.tar.gz", "r:gz") as archive:
            archive.extractall(temporary, filter="data")
        restored = {path.relative_to(temporary).as_posix(): {"size": path.stat().st_size, "sha256": sha256(path)}
                    for path in Path(temporary).rglob("*") if path.is_file()}
        if restored != manifest["storage_files"]:
            raise ValueError("Restored storage differs from backup")
    return {"verified_at": utcnow().isoformat(), "method": "disposable-postgresql-18-restore",
            "manifest_sha256": sha256(Path(directory) / "manifest.json"), "database": DATABASE, "schemas": ["public"],
            "tables_checked": len(actual["table_counts"]), "asset_references_checked": len(actual["asset_references"]),
            "storage_files_checked": len(manifest["storage_files"])}


def latest_success(root):
    for path in generated_sets(root):
        try:
            manifest = read_json(path / "manifest.json")
            if manifest.get("generator") == GENERATOR:
                return datetime.fromisoformat(manifest["completed_at"]), path.name
        except (ValueError, KeyError, OSError):
            pass
    return None, None


def status(root):
    last, name = latest_success(root)
    try:
        state = read_json(Path(root) / "status.json")
    except (ValueError, OSError):
        state = {"state": "unknown"}
    report = {**state, "latest_backup": name, "last_success_at": last.isoformat() if last else None}
    report["healthy"] = last is not None and utcnow() - last < timedelta(hours=26) and state.get("state") != "failed"
    if name:
        try:
            report["restore_verification"] = read_json(Path(root) / name / "restore-verification.json")
        except (ValueError, OSError):
            report["restore_verification"] = None
    return report


def run_once(root, storage, retain):
    try:
        name = create_backup(root, storage, retain)
    except BackupBusy:
        raise  # The lock owner alone updates shared status.
    except Exception as exc:
        write_json(Path(root) / "status.json", {"state": "failed", "last_attempt_at": utcnow().isoformat(), "error": str(exc)})
        raise
    print(json.dumps({"backup": name, "integrity": "passed"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("daemon", "run-once", "status", "health", "check", "verify-restored"))
    parser.add_argument("directory", nargs="?")
    args = parser.parse_args()
    root = Path(os.environ.get("BACKUP_ROOT", "/backups"))
    if args.action == "check":
        verify_integrity(Path(args.directory))
        print("Backup integrity passed")
        return 0
    if args.action == "verify-restored":
        print(json.dumps(verify_restored(Path(args.directory))))
        return 0
    root.mkdir(parents=True, exist_ok=True)
    if args.action in ("status", "health"):
        report = status(root)
        if args.action == "status":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["healthy"] else 1
    storage = Path(os.environ.get("BACKUP_STORAGE", "/app/storage"))
    retain = int(os.environ.get("BACKUP_RETAIN_COUNT", "14"))
    if retain < 1:
        raise ValueError("BACKUP_RETAIN_COUNT must be at least 1")
    if args.action == "run-once":
        run_once(root, storage, retain)
        return 0
    zone = ZoneInfo(os.environ.get("BACKUP_TIMEZONE", "Asia/Bangkok"))
    daily = os.environ.get("BACKUP_DAILY_TIME", "03:00")
    retry_seconds = int(os.environ.get("BACKUP_RETRY_SECONDS", "900"))
    if retry_seconds < 30:
        raise ValueError("BACKUP_RETRY_SECONDS must be at least 30")
    retry_after = utcnow()
    print("Automatic backups enabled; schedule " + daily + " " + str(zone), flush=True)
    while True:
        now = utcnow()
        report = status(root)
        last = datetime.fromisoformat(report["last_success_at"]) if report["last_success_at"] else None
        # Retention can fail after publishing a valid set. Retry that failure
        # even though the new set means the daily capture is no longer due.
        if now >= retry_after and (report.get("state") == "failed" or backup_due(last, now, zone, daily)):
            try:
                run_once(root, storage, retain)
            except BackupBusy:
                print("Backup already running; retrying later", flush=True)
                retry_after = utcnow() + timedelta(seconds=retry_seconds)
            except Exception as exc:
                print("Backup failed: " + str(exc), file=sys.stderr, flush=True)
                retry_after = utcnow() + timedelta(seconds=retry_seconds)
        time.sleep(30)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Backup error: " + str(error), file=sys.stderr)
        sys.exit(1)
