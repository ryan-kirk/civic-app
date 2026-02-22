from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .api.routes import router as api_router
from .db import Base, engine, get_db
from .ingest import ingest_meeting, ingest_range
from .jobs import create_ingest_job, get_job, start_ingest_job, count_active_jobs, most_recent_job_created_at

MAX_INGEST_RANGE_DAYS = 180
INGEST_JOB_COOLDOWN_SECONDS = 10
MAX_ACTIVE_INGEST_JOBS = 1


def _parse_iso_date(s: str):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _validate_ingest_range_request(from_date: str, to_date: str):
    try:
        start = _parse_iso_date(from_date)
        end = _parse_iso_date(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date_format_use_yyyy_mm_dd")
    if end < start:
        raise HTTPException(status_code=400, detail="to_date_must_be_on_or_after_from_date")
    span = (end - start).days + 1
    if span > MAX_INGEST_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"date_range_too_large_max_{MAX_INGEST_RANGE_DAYS}_days")


def _enforce_ingest_job_throttle():
    active = count_active_jobs()
    if active >= MAX_ACTIVE_INGEST_JOBS:
        raise HTTPException(status_code=429, detail="ingest_job_limit_reached_try_again_later")
    latest = most_recent_job_created_at()
    if latest is not None:
        delta = datetime.now().timestamp() - float(latest)
        if delta < INGEST_JOB_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"ingest_job_cooldown_active_wait_{int(max(1, INGEST_JOB_COOLDOWN_SECONDS - delta))}_seconds",
            )

Base.metadata.create_all(bind=engine)

app = FastAPI(title="CivicWatch (Urbandale)")
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
def ui_home():
    return FileResponse("app/static/index.html")


@app.get("/app", include_in_schema=False)
def ui_app():
    return FileResponse("app/static/index.html")

@app.post("/ingest/meeting/{meeting_id}")
def ingest_one(meeting_id: int, store_raw: bool = True, db: Session = Depends(get_db)):
    return ingest_meeting(db, meeting_id, store_raw=store_raw)

@app.post("/ingest/range")
def ingest_dates(
    from_date: str,
    to_date: str,
    limit: int = 50,
    crawl: bool = True,
    chunk_days: int = 31,
    store_raw: bool = True,
    use_recent_cache: bool = True,
    cache_ttl_minutes: int = 60,
    db: Session = Depends(get_db),
):
    _validate_ingest_range_request(from_date, to_date)
    return ingest_range(
        db,
        from_date,
        to_date,
        limit=limit,
        crawl=crawl,
        chunk_days=chunk_days,
        store_raw=store_raw,
        use_recent_cache=use_recent_cache,
        cache_ttl_minutes=cache_ttl_minutes,
    )


@app.post("/ingest/range/job")
def ingest_range_job(
    from_date: str,
    to_date: str,
    limit: int = 50,
    crawl: bool = True,
    chunk_days: int = 31,
    store_raw: bool = True,
    use_recent_cache: bool = True,
    cache_ttl_minutes: int = 60,
):
    _validate_ingest_range_request(from_date, to_date)
    _enforce_ingest_job_throttle()
    job_id = create_ingest_job(
        {
            "from_date": from_date,
            "to_date": to_date,
            "limit": limit,
            "crawl": crawl,
            "chunk_days": chunk_days,
            "store_raw": store_raw,
            "use_recent_cache": use_recent_cache,
            "cache_ttl_minutes": cache_ttl_minutes,
        }
    )
    start_ingest_job(job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/ingest/range/job/{job_id}")
def ingest_range_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job
