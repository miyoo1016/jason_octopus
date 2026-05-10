from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AnalysisJob:
    job_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "queued"
    progress: float = 0.0
    current_node: str | None = None
    current_node_type: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    node_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def elapsed_sec(self) -> float:
        return round(time.time() - self.created_at, 1)

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "current_node": self.current_node,
            "current_node_type": self.current_node_type,
            "elapsed_sec": self.elapsed_sec,
            "error": self.error,
            "node_events": self.node_events[-20:],
        }


class AnalysisJobStore:
    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, fn: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]]) -> AnalysisJob:
        job = AnalysisJob(job_id="job_" + uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id, fn)
        return job

    def get(self, job_id: str) -> AnalysisJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _patch(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in updates.items():
                setattr(job, key, value)
            job.updated_at = time.time()

    def _run(self, job_id: str, fn: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]]) -> None:
        self._patch(job_id, status="running")

        def progress_callback(event: dict[str, Any]) -> None:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                node_id = event.get("node_id")
                if node_id:
                    job.current_node = node_id
                    job.current_node_type = event.get("node_type")
                if "progress" in event:
                    job.progress = max(job.progress, float(event.get("progress") or 0.0))
                job.node_events.append(event)
                job.updated_at = time.time()

        try:
            result = fn(progress_callback)
            success = bool(result.get("success", False))
            self._patch(
                job_id,
                status="completed" if success else "failed",
                progress=1.0,
                result=result if success else None,
                error=None if success else result.get("error") or "분석 실패",
            )
        except Exception as exc:
            self._patch(job_id, status="failed", progress=1.0, error=str(exc))


analysis_jobs = AnalysisJobStore()
