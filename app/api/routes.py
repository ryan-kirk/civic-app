from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models import AgendaItem, Document
from app.services.civicweb_client import CivicWebClient
from app.classifiers.topics import classify_topics
from app.extractors.zoning import extract_zoning_signals
from app.schemas import AgendaItemOut, DocumentOut, ZoningSignalsOut
from app.utils.text import normalize_text

router = APIRouter()
client = CivicWebClient(base_url=settings.civicweb_base_url)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/meetings")
async def list_meetings(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query("9999-12-31", description="YYYY-MM-DD"),
):
    meetings = await client.list_meetings(date_from=date_from, date_to=date_to)
    return {"count": len(meetings), "items": meetings}


@router.get("/meetings/{meeting_id}")
async def meeting_data(meeting_id: int):
    data = await client.get_meeting_data(meeting_id)
    return data

@router.get("/meetings/{meeting_id}/agenda", response_model=list[AgendaItemOut])
def get_agenda(
    meeting_id: int,
    topic: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    items = (
        db.query(AgendaItem)
        .filter(AgendaItem.meeting_id == meeting_id)
        .order_by(AgendaItem.item_key.asc())
        .all()
    )

    # compute topics
    topic_filter = normalize_text(topic or "").lower()
    enriched = []
    for it in items:
        docs = (
            db.query(Document)
            .filter(Document.agenda_item_id == it.id)
            .all()
        )
        normalized_title = normalize_text(it.title)
        docs_text = " ".join(normalize_text(d.title) for d in docs)
        topics = classify_topics(normalized_title, docs_text)
        if topic_filter and topic_filter not in topics:
            continue
        enriched.append((it, docs, normalized_title, topics))

    out = []
    for (it, docs, normalized_title, topics) in enriched:
        docs_out = [
            DocumentOut(
                document_id=d.document_id,
                title=normalize_text(d.title),
                url=d.url,
                handle=d.handle,
            )
            for d in docs
        ]
        docs_text = " ".join(d.title for d in docs_out)
        zoning_signals = (
            ZoningSignalsOut(**extract_zoning_signals(normalized_title, docs_text))
            if "zoning" in topics
            else None
        )

        out.append(
            AgendaItemOut(
                item_key=it.item_key,
                section=it.section,
                title=normalized_title,
                topics=sorted(list(topics)),
                zoning_signals=zoning_signals,
                documents=docs_out,
            )
        )

    return out
