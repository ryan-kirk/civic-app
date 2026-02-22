from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .api.routes import router as api_router
from .db import Base, engine, get_db
from .ingest import ingest_meeting, ingest_range
from .jobs import create_ingest_job, get_job, start_ingest_job

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
    db: Session = Depends(get_db),
):
    return ingest_range(
        db,
        from_date,
        to_date,
        limit=limit,
        crawl=crawl,
        chunk_days=chunk_days,
        store_raw=store_raw,
    )


@app.post("/ingest/range/job")
def ingest_range_job(
    from_date: str,
    to_date: str,
    limit: int = 50,
    crawl: bool = True,
    chunk_days: int = 31,
    store_raw: bool = True,
):
    job_id = create_ingest_job(
        {
            "from_date": from_date,
            "to_date": to_date,
            "limit": limit,
            "crawl": crawl,
            "chunk_days": chunk_days,
            "store_raw": store_raw,
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
