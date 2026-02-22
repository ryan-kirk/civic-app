from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .db import SessionLocal
from .ingest import ingest_range


_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def create_ingest_job(params: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "params": params,
            "progress": {
                "stage": "queued",
                "discovered": 0,
                "processed": 0,
                "current_meeting_id": None,
                "succeeded": 0,
                "failed": 0,
            },
            "result": None,
            "error": None,
        }
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return dict(job)


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = _now()


def start_ingest_job(job_id: str) -> None:
    thread = threading.Thread(target=_run_ingest_job, args=(job_id,), daemon=True)
    thread.start()


def _run_ingest_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    params = job["params"]
    _update_job(job_id, status="running", progress={**job["progress"], "stage": "running"})

    def progress_callback(progress: dict[str, Any]) -> None:
        current = get_job(job_id)
        if not current:
            return
        merged = dict(current["progress"])
        merged.update(progress)
        _update_job(job_id, progress=merged)

    db = SessionLocal()
    try:
        result = ingest_range(
            db=db,
            from_date=params["from_date"],
            to_date=params["to_date"],
            limit=params.get("limit", 50),
            crawl=params.get("crawl", True),
            chunk_days=params.get("chunk_days", 31),
            store_raw=params.get("store_raw", True),
            progress_callback=progress_callback,
        )
        _update_job(job_id, status="completed", result=result)
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))
    finally:
        db.close()
