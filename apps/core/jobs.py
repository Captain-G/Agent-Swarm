"""
Lightweight background jobs (no Redis or Celery required).

* ``JOB_RUNNER=thread`` (default): jobs run in daemon threads inside the web
  process. Zero setup, ideal for a laptop.
* ``JOB_RUNNER=worker``: jobs stay queued until ``python manage.py run_worker``
  picks them up. More robust for servers (web restarts do not kill jobs).

Every job function has the signature ``func(ctx: JobContext, **payload)`` and
may return a JSON-serialisable dict stored as the job result.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import timedelta
from typing import Any, Callable

from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .config import cfg
from .models import Job

logger = logging.getLogger("baraza.jobs")

HEARTBEAT_SECONDS = 20
STALE_AFTER = timedelta(minutes=3)
MAX_LOG_LINES = 400

_semaphore_lock = threading.Lock()
_semaphore: threading.BoundedSemaphore | None = None
_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


class JobCancelled(Exception):
    """Raised inside a job when the user asked to stop it."""


def _get_semaphore() -> threading.BoundedSemaphore:
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.BoundedSemaphore(max(1, int(cfg("MAX_CONCURRENT_JOBS", 2))))
        return _semaphore


class JobContext:
    """Handle passed to job functions for progress reporting and cancellation."""

    def __init__(self, job: Job, *, span: tuple[float, float] = (0.0, 100.0), parent: "JobContext | None" = None):
        self.job_id = job.pk
        self.project_id = job.project_id
        self.kind = job.kind
        self._span = span
        self._parent = parent
        self._log: list = list(job.log or []) if parent is None else []
        self._last_write = 0.0
        self._last_cancel_check = 0.0
        self._cancelled = False
        self._stage = job.stage

    # -- progress -------------------------------------------------------
    def _map(self, pct: float) -> float:
        lo, hi = self._span
        return lo + (hi - lo) * max(0.0, min(100.0, pct)) / 100.0

    def progress(self, pct: float | None = None, stage: str | None = None, message: str | None = None, *, force: bool = False):
        if self._parent is not None:
            self._parent.progress(self._map(pct) if pct is not None else None, stage, message, force=force)
            return
        now = time.monotonic()
        stage_changed = stage is not None and stage != self._stage
        if not (force or stage_changed or now - self._last_write >= 0.75):
            return
        fields: dict[str, Any] = {"heartbeat_at": timezone.now()}
        if pct is not None:
            fields["progress"] = round(self._map(pct), 2)
        if stage is not None:
            fields["stage"] = stage[:160]
            self._stage = stage
        if message is not None:
            fields["message"] = message
        fields["log"] = self._log[-MAX_LOG_LINES:]
        Job.objects.filter(pk=self.job_id).update(**fields)
        self._last_write = now

    def log(self, message: str, level: str = "info"):
        if self._parent is not None:
            self._parent.log(message, level)
            return
        stamp = timezone.localtime().strftime("%H:%M:%S")
        self._log.append([stamp, level, str(message)[:2000]])
        getattr(logger, level if level in {"info", "warning", "error", "debug"} else "info")(
            "[job %s] %s", str(self.job_id)[:8], message
        )
        self.progress(force=level in {"warning", "error"})

    def warn(self, message: str):
        self.log(message, "warning")

    def sub(self, start: float, end: float) -> "JobContext":
        """Child context that maps 0-100 progress into [start, end] of this one."""
        child = JobContext.__new__(JobContext)
        child.job_id = self.job_id
        child.project_id = self.project_id
        child.kind = self.kind
        child._span = (start, end)
        child._parent = self
        child._log = []
        child._last_write = 0.0
        child._last_cancel_check = 0.0
        child._cancelled = False
        child._stage = self._stage
        return child

    # -- cancellation ---------------------------------------------------
    def is_cancelled(self) -> bool:
        if self._parent is not None:
            return self._parent.is_cancelled()
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._last_cancel_check >= 1.0:
            self._last_cancel_check = now
            self._cancelled = bool(
                Job.objects.filter(pk=self.job_id).values_list("cancel_requested", flat=True).first()
            )
        return self._cancelled

    def check_cancel(self):
        if self.is_cancelled():
            raise JobCancelled()

    def flush(self):
        if self._parent is not None:
            self._parent.flush()
            return
        self.progress(force=True)


class _Heartbeat(threading.Thread):
    def __init__(self, job_id):
        super().__init__(daemon=True, name=f"heartbeat-{str(job_id)[:8]}")
        self.job_id = job_id
        self._stop_event = threading.Event()

    def run(self):
        try:
            while not self._stop_event.wait(HEARTBEAT_SECONDS):
                Job.objects.filter(pk=self.job_id, status=Job.Status.RUNNING).update(heartbeat_at=timezone.now())
        except Exception:  # pragma: no cover - best effort
            pass
        finally:
            connection.close()

    def stop(self):
        self._stop_event.set()


def execute_job(job_id) -> Job | None:
    """Claim and run a queued job synchronously in the current thread."""
    if not connection.in_atomic_block:  # inline runs inside a transaction (tests) keep their connection
        close_old_connections()
    now = timezone.now()
    claimed = Job.objects.filter(pk=job_id, status=Job.Status.QUEUED).update(
        status=Job.Status.RUNNING, started_at=now, heartbeat_at=now, stage="Starting"
    )
    if not claimed:
        return Job.objects.filter(pk=job_id).first()
    job = Job.objects.get(pk=job_id)
    ctx = JobContext(job)
    heartbeat = _Heartbeat(job.pk)
    heartbeat.start()
    status, error, result = Job.Status.SUCCEEDED, "", {}
    try:
        func: Callable = import_string(job.func)
        result = func(ctx, **(job.payload or {})) or {}
        if not isinstance(result, dict):
            result = {"value": result}
    except JobCancelled:
        status = Job.Status.CANCELLED
        ctx.log("Stopped by user.", "warning")
    except Exception as exc:  # noqa: BLE001 - we record every failure
        status = Job.Status.FAILED
        error = f"{exc.__class__.__name__}: {exc}\n\n{traceback.format_exc()}"
        ctx.log(f"Failed: {exc}", "error")
        logger.exception("Job %s failed", job.pk)
    finally:
        heartbeat.stop()
    fields = {
        "status": status,
        "finished_at": timezone.now(),
        "heartbeat_at": timezone.now(),
        "error": error,
        "result": result,
        "log": ctx._log[-MAX_LOG_LINES:],
    }
    if status == Job.Status.SUCCEEDED:
        fields["progress"] = 100.0
        fields["stage"] = "Done"
    Job.objects.filter(pk=job.pk).update(**fields)
    return Job.objects.get(pk=job.pk)


def _thread_target(job_id):
    sem = _get_semaphore()
    sem.acquire()
    try:
        execute_job(job_id)
    finally:
        sem.release()
        with _threads_lock:
            _threads.pop(str(job_id), None)
        connection.close()


def _start_thread(job_id):
    key = str(job_id)
    with _threads_lock:
        if key in _threads and _threads[key].is_alive():
            return
        thread = threading.Thread(target=_thread_target, args=(job_id,), daemon=True, name=f"job-{key[:8]}")
        _threads[key] = thread
    thread.start()


def enqueue(
    func: str,
    *,
    kind: str,
    label: str,
    project=None,
    payload: dict | None = None,
) -> Job:
    """Create a job and (in thread mode) start it after the transaction commits."""
    job = Job.objects.create(func=func, kind=kind, label=label[:200], project=project, payload=payload or {})
    if cfg("JOB_RUNNER", "thread") == "thread":
        transaction.on_commit(lambda: _start_thread(job.pk))
    return job


def dispatch_pending() -> None:
    """Thread mode: (re)start queued jobs orphaned by a previous process. Also flags stale jobs."""
    mark_stale_jobs()
    if cfg("JOB_RUNNER", "thread") != "thread":
        return
    cutoff = timezone.now() - timedelta(seconds=5)
    for job_id in Job.objects.filter(status=Job.Status.QUEUED, created_at__lt=cutoff).values_list("pk", flat=True)[:10]:
        _start_thread(job_id)


def mark_stale_jobs() -> int:
    cutoff = timezone.now() - STALE_AFTER
    return Job.objects.filter(status=Job.Status.RUNNING, heartbeat_at__lt=cutoff).update(
        status=Job.Status.INTERRUPTED,
        finished_at=timezone.now(),
        message="The server stopped while this job was running. You can resume or restart it.",
    )


def cancel(job: Job) -> None:
    if job.status == Job.Status.QUEUED:
        Job.objects.filter(pk=job.pk, status=Job.Status.QUEUED).update(
            status=Job.Status.CANCELLED, cancel_requested=True, finished_at=timezone.now()
        )
    Job.objects.filter(pk=job.pk).update(cancel_requested=True)


def active_job(project=None, kinds: list[str] | tuple[str, ...] | None = None) -> Job | None:
    qs = Job.objects.filter(status__in=Job.ACTIVE_STATUSES)
    if project is not None:
        qs = qs.filter(project=project)
    if kinds:
        qs = qs.filter(kind__in=kinds)
    return qs.order_by("-created_at").first()


def run_inline(func: str, *, kind: str, label: str, project=None, payload: dict | None = None) -> Job:
    """Create and run a job synchronously (management commands and tests)."""
    job = Job.objects.create(func=func, kind=kind, label=label[:200], project=project, payload=payload or {})
    return execute_job(job.pk)
