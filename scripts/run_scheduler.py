"""Run one maintenance scheduler without starting an HTTP server."""

from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasks import start_scheduler, stop_scheduler


def main() -> None:
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    start_scheduler()
    try:
        stopped.wait()
    finally:
        stop_scheduler()


if __name__ == "__main__":
    main()
