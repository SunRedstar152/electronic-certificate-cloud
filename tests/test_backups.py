"""Run without the application test DB: python -m unittest discover -s tests -p test_backups.py."""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "backup.py"


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location("backup", MODULE)
            cls.backup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backup)
        else:
            cls.backup = None

    def setUp(self):
        self.assertIsNotNone(self.backup, "Backup tooling has not been implemented")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_daily_backup_catches_up_after_machine_was_off(self):
        now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
        self.assertTrue(self.backup.backup_due(None, now, timezone.utc, "03:00"))
        self.assertTrue(self.backup.backup_due(now - timedelta(days=2), now, timezone.utc, "03:00"))
        self.assertTrue(self.backup.backup_due(now.replace(hour=2), now, timezone.utc, "03:00"))
        self.assertFalse(self.backup.backup_due(now.replace(hour=4), now, timezone.utc, "03:00"))
        self.assertFalse(self.backup.backup_due(now - timedelta(hours=12), now.replace(hour=1), timezone.utc, "03:00"))

    def make_set(self, name="auto-20260909T030000Z-12345678"):
        source = self.root / (name + "-source")
        (source / "uploads/templates").mkdir(parents=True)
        (source / "uploads/templates/test.png").write_bytes(b"example image")
        target = self.root / name
        target.mkdir()
        inventory = self.backup.archive_storage(source, target / "storage.tar.gz")
        (target / "database.dump").write_bytes(b"example dump")
        manifest = {
            "format_version": 1, "generator": "pkru-certificate-backup",
            "completed_at": "2026-09-09T03:00:00+00:00", "database": "pkru_certificate",
            "schemas": ["public"], "table_counts": {"templates": 1},
            "asset_references": [{"table": "templates", "id": 1, "path": "/app/storage/uploads/templates/test.png"}],
            "storage_files": inventory,
            "checksums": {file: self.backup.sha256(target / file) for file in ("database.dump", "storage.tar.gz")},
        }
        (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return target, manifest

    def test_valid_archive_checks_all_files_and_database_references(self):
        target, manifest = self.make_set()
        self.assertEqual(self.backup.verify_integrity(target), manifest)
        self.assertEqual(self.backup.asset_member("builtin:original"), None)
        self.assertEqual(self.backup.asset_member("storage/uploads/templates/test.png"), "storage/uploads/templates/test.png")

    def test_corrupt_dump_is_rejected(self):
        target, _ = self.make_set()
        (target / "database.dump").write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.backup.verify_integrity(target)

    def test_missing_referenced_asset_is_rejected(self):
        target, manifest = self.make_set()
        manifest["asset_references"][0]["path"] = "storage/uploads/templates/missing.png"
        (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing referenced"):
            self.backup.verify_integrity(target)

    def test_archive_traversal_and_links_are_rejected_even_with_matching_checksum(self):
        for malicious in ("storage/../../escape", "storage/link"):
            with self.subTest(malicious=malicious):
                target, manifest = self.make_set("auto-20260909T030000Z-" + ("11111111" if "escape" in malicious else "22222222"))
                with tarfile.open(target / "storage.tar.gz", "w:gz") as archive:
                    info = tarfile.TarInfo(malicious)
                    if "link" in malicious:
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/etc/passwd"
                    archive.addfile(info, io.BytesIO(b""))
                manifest["checksums"]["storage.tar.gz"] = self.backup.sha256(target / "storage.tar.gz")
                (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    self.backup.verify_integrity(target)

    def test_retention_preserves_manual_partial_and_corrupt_sets(self):
        older, _ = self.make_set("auto-20260908T030000Z-11111111")
        newer, _ = self.make_set("auto-20260909T030000Z-22222222")
        corrupt, _ = self.make_set("auto-20260907T030000Z-33333333")
        (corrupt / "database.dump").write_bytes(b"damaged")
        manual = self.root / "manual-backup"
        manual.mkdir()
        partial = self.root / ".auto-20260906T030000Z-44444444.partial"
        partial.mkdir()
        self.backup.apply_retention(self.root, 1)
        self.assertFalse(older.exists())
        for kept in (newer, corrupt, manual, partial):
            self.assertTrue(kept.exists())

    def test_concurrent_backup_does_not_overwrite_owners_status(self):
        self.assertTrue(hasattr(self.backup, "BackupBusy"), "A busy backup must have its own outcome")
        owner = {"state": "running", "last_attempt_at": "2026-09-09T03:00:00+00:00"}
        self.backup.write_json(self.root / "status.json", owner)
        with patch.object(self.backup, "create_backup", side_effect=self.backup.BackupBusy("Another backup is running")):
            with self.assertRaises(self.backup.BackupBusy):
                self.backup.run_once(self.root, self.root / "storage", 14)
        self.assertEqual(self.backup.read_json(self.root / "status.json"), owner)

    def test_restore_refuses_wrong_database_before_running_pg_restore(self):
        target, _ = self.make_set()
        with patch.dict(os.environ, {"BACKUP_DISPOSABLE_VERIFY": "1", "PGHOST": "127.0.0.1"}), \
                patch.object(self.backup, "PgSession") as sessions, \
                patch.object(self.backup, "tables", return_value=[]), \
                patch.object(self.backup, "run_command") as command:
            sessions.return_value.__enter__.return_value.query.return_value = "postgres"
            with self.assertRaisesRegex(ValueError, "database must be pkru_certificate"):
                self.backup.verify_restored(target)
            command.assert_not_called()

    def test_daemon_retries_failed_retention_even_when_latest_backup_is_fresh(self):
        class StopDaemon(Exception):
            pass

        now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
        report = {"state": "failed", "last_success_at": now.isoformat()}
        with patch.dict(os.environ, {"BACKUP_ROOT": str(self.root), "BACKUP_TIMEZONE": "UTC", "BACKUP_DAILY_TIME": "03:00"}), \
                patch.object(sys, "argv", ["backup.py", "daemon"]), \
                patch.object(self.backup, "utcnow", return_value=now), \
                patch.object(self.backup, "latest_success", return_value=(now, "fresh-backup")), \
                patch.object(self.backup, "status", return_value=report), \
                patch.object(self.backup, "run_once") as run, \
                patch.object(self.backup.time, "sleep", side_effect=StopDaemon):
            with self.assertRaises(StopDaemon):
                self.backup.main()
            run.assert_called_once()

    def test_restore_refuses_unsupported_postgresql_before_running_pg_restore(self):
        target, _ = self.make_set()
        with patch.dict(os.environ, {"BACKUP_DISPOSABLE_VERIFY": "1", "PGHOST": "127.0.0.1"}), \
                patch.object(self.backup, "PgSession") as sessions, \
                patch.object(self.backup, "tables", return_value=[]), \
                patch.object(self.backup, "run_command") as command:
            sessions.return_value.__enter__.return_value.query.side_effect = ["pkru_certificate", "170006"]
            with self.assertRaisesRegex(ValueError, "PostgreSQL 18 is required"):
                self.backup.verify_restored(target)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
