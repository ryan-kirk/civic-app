from sqlalchemy.orm import Session
from . import civicweb_client as cw
from .parser import parse_agenda_html
from .models import Meeting, AgendaItem, Document

def upsert_meeting(db: Session, meeting_id: int, meeting_data: dict):
    m = db.get(Meeting, meeting_id)
    if not m:
        m = Meeting(meeting_id=meeting_id)
        db.add(m)

    m.name = meeting_data.get("Name", "") or ""
    m.location = meeting_data.get("Location", "") or ""
    m.time = meeting_data.get("Time", "") or ""
    m.type_id = int(meeting_data.get("TypeId") or 0)
    m.video_url = meeting_data.get("MeetingExternalLinkUrl", "") or ""

    # Meeting date: for now parse from name if present (e.g. "City Council - February 17, 2026")
    # Later we can extract from list_meetings response if it includes an actual date field.
    m.date = ""
    return m

def ingest_meeting(db: Session, meeting_id: int):
    meeting_data = cw.get_meeting_data(meeting_id)
    meeting = upsert_meeting(db, meeting_id, meeting_data)

    docs = cw.get_meeting_documents(meeting_id)

    # Find agenda html doc container
    agenda_html = None
    for d in docs:
        if int(d.get("DocumentType") or 0) == 1 and d.get("Html"):
            agenda_html = d["Html"]
            break

    if not agenda_html:
        db.commit()
        return {"meeting_id": meeting_id, "status": "no_agenda_html"}

    parsed_items = parse_agenda_html(agenda_html)

    # Upsert agenda items + documents
    for it in parsed_items:
        item = db.query(AgendaItem).filter(
            AgendaItem.meeting_id == meeting_id,
            AgendaItem.item_key == it["item_key"]
        ).one_or_none()

        if not item:
            item = AgendaItem(meeting_id=meeting_id, item_key=it["item_key"])
            db.add(item)

        item.section = it.get("section", "") or ""
        item.title = it.get("title", "") or ""

        db.flush()  # to get item.id

        for att in it.get("attachments", []):
            doc = db.query(Document).filter(
                Document.meeting_id == meeting_id,
                Document.document_id == att["document_id"]
            ).one_or_none()

            if not doc:
                doc = Document(meeting_id=meeting_id, document_id=att["document_id"])
                db.add(doc)

            doc.agenda_item_id = item.id
            doc.title = att.get("title", "") or ""
            doc.url = att.get("url", "") or ""
            doc.handle = att.get("handle", "") or ""

    db.commit()
    return {"meeting_id": meeting_id, "status": "ok", "agenda_items": len(parsed_items)}

def ingest_range(db: Session, from_date: str, to_date: str, limit: int = 50):
    meetings = cw.list_meetings(from_date, to_date)
    # meetings response shape can vary; we’ll assume it’s a list of dicts containing Id
    ids = []
    for m in meetings:
        mid = m.get("Id")
        if isinstance(mid, int):
            ids.append(mid)

    ids = ids[:limit]
    results = []
    for mid in ids:
        results.append(ingest_meeting(db, mid))
    return results