from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import Meeting, AgendaItem, Document
from .schemas import MeetingOut, AgendaItemOut, DocumentOut
from .ingest import ingest_meeting, ingest_range

Base.metadata.create_all(bind=engine)

app = FastAPI(title="CivicWatch (Urbandale)")

@app.get("/meetings", response_model=list[MeetingOut])
def list_meetings(db: Session = Depends(get_db)):
    rows = db.query(Meeting).order_by(Meeting.meeting_id.desc()).limit(200).all()
    return [
        MeetingOut(
            meeting_id=m.meeting_id,
            name=m.name,
            date=m.date,
            time=m.time,
            location=m.location,
            type_id=m.type_id,
            video_url=m.video_url,
        )
        for m in rows
    ]

@app.get("/meetings/{meeting_id}/agenda", response_model=list[AgendaItemOut])
def get_agenda(meeting_id: int, db: Session = Depends(get_db)):
    items = (
        db.query(AgendaItem)
        .filter(AgendaItem.meeting_id == meeting_id)
        .order_by(AgendaItem.item_key.asc())
        .all()
    )

    out = []
    for it in items:
        docs = (
            db.query(Document)
            .filter(Document.agenda_item_id == it.id)
            .all()
        )
        out.append(AgendaItemOut(
            item_key=it.item_key,
            section=it.section,
            title=it.title,
            documents=[DocumentOut(
                document_id=d.document_id,
                title=d.title,
                url=d.url,
                handle=d.handle
            ) for d in docs]
        ))
    return out

@app.post("/ingest/meeting/{meeting_id}")
def ingest_one(meeting_id: int, db: Session = Depends(get_db)):
    return ingest_meeting(db, meeting_id)

@app.post("/ingest/range")
def ingest_dates(from_date: str, to_date: str, limit: int = 50, db: Session = Depends(get_db)):
    return ingest_range(db, from_date, to_date, limit=limit)