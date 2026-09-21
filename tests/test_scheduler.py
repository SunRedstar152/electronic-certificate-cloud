from fastapi.testclient import TestClient

import main
import tasks


def test_application_lifespan_does_not_start_a_disabled_scheduler(monkeypatch):
    """Starting unconditionally would run cleanup from every web worker."""
    calls = []
    monkeypatch.setattr(main.settings, "ENABLE_SCHEDULER", False)
    monkeypatch.setattr(main, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(main, "stop_scheduler", lambda: calls.append("stop"))

    with TestClient(main.app):
        assert calls == []

    assert calls == []


def test_application_lifespan_starts_and_stops_an_enabled_scheduler_once(monkeypatch):
    """A designated scheduler process must start and stop cleanup exactly once."""
    calls = []
    monkeypatch.setattr(main.settings, "ENABLE_SCHEDULER", True)
    monkeypatch.setattr(main, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(main, "stop_scheduler", lambda: calls.append("stop"))

    with TestClient(main.app):
        assert calls == ["start"]

    assert calls == ["start", "stop"]


def test_scheduler_start_and_stop_are_idempotent(monkeypatch):
    """Repeated process hooks must not start or stop the APScheduler twice."""
    class Scheduler:
        running = False

        def __init__(self):
            self.jobs = []
            self.starts = 0
            self.stops = 0

        def add_job(self, *args, **kwargs):
            self.jobs.append((args, kwargs))

        def start(self):
            self.starts += 1
            self.running = True

        def shutdown(self, wait):
            self.stops += 1
            self.running = False

    scheduler = Scheduler()
    monkeypatch.setattr(tasks, "scheduler", scheduler)

    tasks.start_scheduler()
    tasks.start_scheduler()
    tasks.stop_scheduler()
    tasks.stop_scheduler()

    assert scheduler.starts == 1
    assert scheduler.stops == 1
    assert len(scheduler.jobs) == 2


def test_scheduler_uses_the_validated_application_retention_setting(monkeypatch):
    """Reading os.environ here would bypass the configuration validation boundary."""
    monkeypatch.setattr(tasks.settings, "AUDIT_RETENTION_DAYS", 47)

    assert tasks.audit_retention_days() == 47
