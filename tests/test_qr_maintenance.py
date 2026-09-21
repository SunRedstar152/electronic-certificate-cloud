"""Maintenance integration using real PNGs and a PostgreSQL boundary double."""
from contextlib import contextmanager
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from PIL import Image
import zxingcpp
from test_certificate_qr_update import fixture, CODE, NEW, NEW_BASE


class FakeConnection:
    def __init__(self, rows, *, available=True):
        self.rows = rows
        self.available = available
        self.locked = False
        self.closed = False
        self.queries = []
        self.after_page = None

    def execution_options(self, **kwargs):
        return self

    def scalar(self, statement, parameters=None):
        sql = str(statement)
        if "pg_try_advisory_lock" in sql:
            self.locked = self.available
            return self.available
        if "pg_advisory_unlock" in sql:
            self.locked = False
            return True
        if "max(" in sql:
            return max((row[0] for row in self.rows), default=None)
        raise AssertionError(f"Unexpected database query: {sql}")

    def execute(self, statement):
        params = statement.compile().params
        self.queries.append(params)
        rows = [r for r in self.rows if params["id_1"] < r[0] <= params["id_2"]][:params["param_1"]]
        if self.after_page:
            self.after_page()
        return rows

    def invalidate(self):
        self.locked = False


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        try:
            yield self.connection
        finally:
            self.connection.closed = True


class QrMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import services.qr_maintenance as maintenance
        cls.module = maintenance

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rows = []
        for cid in (1, 4, 7):
            path = self.root / f"{cid}.png"
            fixture(path, metadata=True)
            self.rows.append((cid, CODE, str(path)))
        self.connection = FakeConnection(self.rows)
        self.engine = FakeEngine(self.connection)
        for setting, value in [("CERTIFICATE_DIR", self.root), ("PUBLIC_BASE_URL", NEW_BASE)]:
            setting_patch = patch.object(self.module.settings, setting, value)
            setting_patch.start()
            self.addCleanup(setting_patch.stop)
        engine_patch = patch.object(self.module, "engine", self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)

    def test_batches_update_real_images_and_release_database_lock(self):
        report = self.module.run_qr_maintenance(batch_size=2)
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.counts["updated"], 3)
        self.assertFalse(self.connection.locked)
        self.assertTrue(self.connection.closed)
        self.assertEqual([p["id_1"] for p in self.connection.queries], [0, 4])
        for _, _, path in self.rows:
            with Image.open(path) as image:
                self.assertIn(NEW, [r.text for r in zxingcpp.read_barcodes(image)])

    def test_one_bad_image_does_not_stop_later_files(self):
        Path(self.rows[1][2]).write_bytes(b"damaged image")
        with self.assertLogs(self.module.logger, level="WARNING"):
            report = self.module.run_qr_maintenance(batch_size=1)
        self.assertEqual(report.counts["updated"], 2)
        self.assertEqual(report.counts["failed"], 1)
        self.assertEqual(report.failures[0]["id"], 4)
        self.assertEqual(set(report.failures[0]), {"id", "reason"})
        self.assertFalse(self.connection.locked)

    def test_busy_worker_and_dry_run_do_not_change_files(self):
        before = [Path(row[2]).read_bytes() for row in self.rows]
        self.connection.available = False
        self.assertEqual(self.module.run_qr_maintenance().status, "busy")
        self.connection.available = True
        report = self.module.run_qr_maintenance(dry_run=True)
        self.assertEqual(report.counts["updated"], 3)
        self.assertEqual([Path(row[2]).read_bytes() for row in self.rows], before)

    def test_cancellation_stops_between_pages_and_unlocks(self):
        stop = Event()
        self.connection.after_page = stop.set
        before = Path(self.rows[0][2]).read_bytes()
        report = self.module.run_qr_maintenance(stop_event=stop)
        self.assertEqual(report.status, "cancelled")
        self.assertEqual(report.counts["updated"], 0)
        self.assertEqual(Path(self.rows[0][2]).read_bytes(), before)
        self.assertFalse(self.connection.locked)

    def test_scan_uses_fixed_high_water_id(self):
        new_path = self.root / "later.png"
        fixture(new_path, metadata=True)
        before = new_path.read_bytes()
        def append_later():
            if len(self.rows) == 3:
                self.rows.append((100, CODE, str(new_path)))
        self.connection.after_page = append_later
        report = self.module.run_qr_maintenance(batch_size=1)
        self.assertEqual(report.counts["updated"], 3)
        self.assertEqual(new_path.read_bytes(), before)

    def test_start_returns_while_work_runs_and_stop_joins_the_worker(self):
        entered = Event()
        def slow_work(*, stop_event):
            entered.set()
            stop_event.wait(3)
            return self.module.QrMaintenanceReport(dry_run=False, status="cancelled")
        with patch.object(self.module.settings, "QR_UPDATE_ON_STARTUP", True), \
                patch.object(self.module, "run_qr_maintenance", side_effect=slow_work):
            job = self.module.start_qr_maintenance()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(job.thread.is_alive())
            finally:
                self.module.stop_qr_maintenance(job)
            self.assertFalse(job.thread.is_alive())
        with patch.object(self.module.settings, "QR_UPDATE_ON_STARTUP", False):
            self.assertIsNone(self.module.start_qr_maintenance())

    def test_cli_defaults_to_dry_run_and_requires_apply_to_write(self):
        from scripts.update_certificate_qr import main
        before = Path(self.rows[0][2]).read_bytes()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["counts"]["updated"], 3)
        self.assertEqual(Path(self.rows[0][2]).read_bytes(), before)
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["--apply"]), 0)
        with Image.open(self.rows[0][2]) as image:
            self.assertIn(NEW, [r.text for r in zxingcpp.read_barcodes(image)])

    def test_startup_retries_after_a_competing_dry_run_releases_lock(self):
        run = self.module.run_qr_maintenance
        self.connection.available = False
        complete = Event()
        def first_busy_then_available(*, stop_event):
            report = run(stop_event=stop_event)
            if report.status == "busy":
                self.connection.available = True
            else:
                complete.set()
            return report
        with patch.object(self.module.settings, "QR_UPDATE_ON_STARTUP", True), \
                patch.object(self.module, "run_qr_maintenance", side_effect=first_busy_then_available):
            job = self.module.start_qr_maintenance()
            try:
                self.assertTrue(complete.wait(4), "Startup did not retry the busy maintenance pass")
            finally:
                self.module.stop_qr_maintenance(job)
        with Image.open(self.rows[0][2]) as image:
            self.assertIn(NEW, [r.text for r in zxingcpp.read_barcodes(image)])

    def test_database_timeout_reports_failure_and_releases_lock(self):
        from sqlalchemy.exc import OperationalError
        scalar = self.connection.scalar
        def timeout(statement, parameters=None):
            if "max(" in str(statement):
                raise OperationalError("query", {}, Exception("timeout"))
            return scalar(statement, parameters)
        with patch.object(self.connection, "scalar", side_effect=timeout):
            report = self.module.run_qr_maintenance()
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.error, "database_unavailable")
        self.assertFalse(self.connection.locked)


if __name__ == "__main__":
    unittest.main()
