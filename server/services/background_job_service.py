from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class BackgroundJobService:
    """Submit ingest/sync work items and track their status asynchronously.

    Jobs are executed one at a time (``max_workers=1``) to avoid concurrent
    writes to the in-memory corpus and manifest, which are not independently
    thread-safe.  The caller receives a ``job_id`` immediately and can poll
    ``get_job()`` or ``list_jobs()`` for progress.

    Args:
        max_workers: Size of the internal thread pool.  Defaults to ``1``
            (sequential queue) to protect shared in-memory state.  Raise
            only when corpus/manifest services are replaced with
            concurrency-safe backends.
    """

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ingest-worker",
        )
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """Enqueue *fn* for background execution and return its job ID.

        Args:
            fn: Callable to run in the background.  Its return value is stored
                as ``result`` on completion; any raised exception is stored as
                the sole ``errors`` entry and the job is marked ``failed``.
            *args: Positional arguments forwarded to *fn*.
            **kwargs: Keyword arguments forwarded to *fn*.

        Returns:
            A UUID string identifying the submitted job.  Use it with
            :meth:`get_job` to poll status and retrieve the result.
        """
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "queued_at": _now_iso(),
                "started_at": None,
                "completed_at": None,
                "result": None,
                "errors": [],
            }
        self._executor.submit(self._run, job_id, fn, args, kwargs)
        return job_id

    def _run(
        self,
        job_id: str,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["started_at"] = _now_iso()
        try:
            result = fn(*args, **kwargs)
            errors = result.get("errors", []) if isinstance(result, dict) else []
            with self._lock:
                self._jobs[job_id]["status"] = "completed"
                self._jobs[job_id]["result"] = result
                self._jobs[job_id]["errors"] = errors
                self._jobs[job_id]["completed_at"] = _now_iso()
        except Exception as exc:
            logger.error("Background job %s failed: %s", job_id, exc, exc_info=True)
            with self._lock:
                self._jobs[job_id]["status"] = "failed"
                self._jobs[job_id]["errors"] = [str(exc)]
                self._jobs[job_id]["completed_at"] = _now_iso()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return a snapshot of the job record, or ``None`` if not found.

        Args:
            job_id: UUID string returned by :meth:`submit`.

        Returns:
            A copy of the job dict with keys ``job_id``, ``status``,
            ``queued_at``, ``started_at``, ``completed_at``, ``result``,
            and ``errors``; or ``None`` when *job_id* is unknown.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            return dict(record) if record is not None else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most-recent jobs, newest first.

        Args:
            limit: Maximum number of records to return.  Defaults to ``50``.

        Returns:
            List of job dicts ordered by ``queued_at`` descending.
        """
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j["queued_at"],
                reverse=True,
            )
            return [dict(j) for j in jobs[:limit]]

    def recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return jobs that completed with errors or failed outright, newest first.

        Useful for surfacing background failures inside ``GET /index/status``
        without requiring callers to poll every job individually.

        Args:
            limit: Maximum number of records to return.  Defaults to ``20``.

        Returns:
            List of job dicts where ``errors`` is non-empty, ordered by
            ``completed_at`` descending.
        """
        with self._lock:
            errored = [
                j
                for j in self._jobs.values()
                if j["errors"]
            ]
        errored.sort(key=lambda j: j.get("completed_at") or "", reverse=True)
        return [dict(j) for j in errored[:limit]]

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the internal thread pool.

        Args:
            wait: When ``True``, block until all queued jobs finish.
                Defaults to ``False`` for fast server shutdown.
        """
        self._executor.shutdown(wait=wait)
