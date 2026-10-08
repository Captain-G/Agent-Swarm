"""Process background jobs in a separate process (JOB_RUNNER=worker)."""

import signal
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.core.config import cfg
from apps.core.jobs import execute_job, mark_stale_jobs
from apps.core.models import Job


class Command(BaseCommand):
    help = "Run queued Baraza jobs (use with JOB_RUNNER=worker). Ctrl+C to stop."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the queue once and exit.")
        parser.add_argument("--concurrency", type=int, default=0, help="Parallel jobs (default MAX_CONCURRENT_JOBS).")

    def handle(self, *args, **opts):
        concurrency = opts["concurrency"] or int(cfg("MAX_CONCURRENT_JOBS", 2))
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        running: list[threading.Thread] = []
        self.stdout.write(self.style.SUCCESS(f"Baraza worker started (concurrency {concurrency})."))
        while not stop.is_set():
            close_old_connections()
            mark_stale_jobs()
            running = [t for t in running if t.is_alive()]
            free = concurrency - len(running)
            if free > 0:
                for job_id in Job.objects.filter(status=Job.Status.QUEUED).order_by("created_at").values_list("pk", flat=True)[:free]:
                    thread = threading.Thread(target=execute_job, args=(job_id,), daemon=True)
                    thread.start()
                    running.append(thread)
                    self.stdout.write(f"Started job {job_id}")
            if opts["once"]:
                for t in running:
                    t.join()
                break
            time.sleep(1.5)
        self.stdout.write("Worker stopped.")
