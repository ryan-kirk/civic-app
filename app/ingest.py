import json
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session
from . import civicweb_client as cw
from .document_text import upsert_document_text_extraction_from_document
from .entities import extract_entities_from_text, replace_entity_mentions_for_source
from .graph import rebuild_graph_for_meeting
from .minutes import upsert_minutes_metadata_from_document
from .parser import parse_agenda_html
from .models import (
    Meeting,
    AgendaItem,
    Document,
    DocumentTextExtraction,
    MeetingMinutesMetadata,
    MeetingRawData,
    MeetingRangeDiscoveryCache,
)

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

def upsert_meeting_raw_data(db: Session, meeting_id: int, meeting_data: dict, meeting_documents: list[dict]):
    raw = db.query(MeetingRawData).filter(MeetingRawData.meeting_id == meeting_id).one_or_none()
    if not raw:
        raw = MeetingRawData(meeting_id=meeting_id)
        db.add(raw)

    raw.meeting_data_json = json.dumps(meeting_data or {}, ensure_ascii=True, sort_keys=True)
    raw.meeting_documents_json = json.dumps(meeting_documents or [], ensure_ascii=True, sort_keys=True)
    return raw


def ingest_meeting(db: Session, meeting_id: int, store_raw: bool = True):
    meeting_data = cw.get_meeting_data(meeting_id)
    meeting = upsert_meeting(db, meeting_id, meeting_data)
    meeting_context = " ".join(
        [
            str(meeting_data.get("Name") or ""),
            str(meeting_data.get("Location") or ""),
            str(meeting_data.get("Time") or ""),
        ]
    )
    replace_entity_mentions_for_source(
        db,
        meeting_id=meeting_id,
        source_type="meeting_metadata",
        source_id=meeting_id,
        context_text=meeting_context,
        entities=extract_entities_from_text(meeting_context),
    )

    docs = cw.get_meeting_documents(meeting_id)
    if store_raw:
        upsert_meeting_raw_data(db, meeting_id, meeting_data, docs)

    # Find agenda html doc container
    agenda_html = None
    for d in docs:
        if int(d.get("DocumentType") or 0) == 1 and d.get("Html"):
            agenda_html = d["Html"]
            break

    if not agenda_html:
        rebuild_graph_for_meeting(db, meeting_id)
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

        replace_entity_mentions_for_source(
            db,
            meeting_id=meeting_id,
            agenda_item_id=item.id,
            source_type="agenda_item_title",
            source_id=item.id,
            context_text=item.title,
            entities=extract_entities_from_text(item.title),
        )

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

            upsert_minutes_metadata_from_document(
                db=db,
                meeting_id=meeting_id,
                document_id=doc.document_id,
                title=doc.title,
                url=doc.url,
            )
            doc_text = upsert_document_text_extraction_from_document(
                db=db,
                meeting_id=meeting_id,
                document_id=doc.document_id,
                title=doc.title,
                url=doc.url,
            )
            replace_entity_mentions_for_source(
                db,
                meeting_id=meeting_id,
                agenda_item_id=item.id,
                document_id=doc.document_id,
                source_type="document_title",
                source_id=doc.id if getattr(doc, "id", None) is not None else doc.document_id,
                context_text=doc.title,
                entities=extract_entities_from_text(doc.title),
            )
            if doc_text and doc_text.text_excerpt:
                db.flush()
                replace_entity_mentions_for_source(
                    db,
                    meeting_id=meeting_id,
                    agenda_item_id=item.id,
                    document_id=doc.document_id,
                    source_type="document_content",
                    source_id=doc_text.id,
                    context_text=doc_text.text_excerpt,
                    entities=extract_entities_from_text(doc_text.text_excerpt),
                )

    db.flush()
    minutes_rows = (
        db.query(MeetingMinutesMetadata)
        .filter(MeetingMinutesMetadata.meeting_id == meeting_id)
        .all()
    )
    for m in minutes_rows:
        replace_entity_mentions_for_source(
            db,
            meeting_id=meeting_id,
            document_id=m.document_id,
            source_type="minutes_excerpt",
            source_id=m.id,
            context_text=m.text_excerpt,
            entities=extract_entities_from_text(m.text_excerpt),
        )

    rebuild_graph_for_meeting(db, meeting_id)
    db.commit()
    return {"meeting_id": meeting_id, "status": "ok", "agenda_items": len(parsed_items)}

def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _collect_meeting_ids(from_date: str, to_date: str, chunk_days: int = 31) -> list[int]:
    start = _parse_iso_date(from_date)
    end = _parse_iso_date(to_date)
    if end < start:
        raise ValueError("to_date must be on or after from_date")

    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")

    ids: list[int] = []
    seen: set[int] = set()

    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=chunk_days - 1), end)
        meetings = cw.list_meetings(cursor.isoformat(), window_end.isoformat())

        for m in meetings:
            mid = m.get("Id")
            if isinstance(mid, int) and mid not in seen:
                seen.add(mid)
                ids.append(mid)

        cursor = window_end + timedelta(days=1)

    return ids


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get_range_discovery_cache(
    db: Session,
    *,
    from_date: str,
    to_date: str,
    crawl: bool,
    chunk_days: int,
) -> MeetingRangeDiscoveryCache | None:
    return (
        db.query(MeetingRangeDiscoveryCache)
        .filter(
            MeetingRangeDiscoveryCache.from_date == from_date,
            MeetingRangeDiscoveryCache.to_date == to_date,
            MeetingRangeDiscoveryCache.crawl == (1 if crawl else 0),
            MeetingRangeDiscoveryCache.chunk_days == int(chunk_days),
        )
        .one_or_none()
    )


def _read_cached_meeting_ids(
    db: Session,
    *,
    from_date: str,
    to_date: str,
    crawl: bool,
    chunk_days: int,
    cache_ttl_minutes: int,
) -> tuple[list[int], MeetingRangeDiscoveryCache] | tuple[None, MeetingRangeDiscoveryCache | None]:
    row = _get_range_discovery_cache(
        db,
        from_date=from_date,
        to_date=to_date,
        crawl=crawl,
        chunk_days=chunk_days,
    )
    if not row or not row.last_fetched_at:
        return (None, row)

    try:
        fetched_at = datetime.fromisoformat(row.last_fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return (None, row)

    age = datetime.now(fetched_at.tzinfo) - fetched_at
    if age > timedelta(minutes=max(cache_ttl_minutes, 0)):
        return (None, row)

    try:
        ids_raw = json.loads(row.meeting_ids_json or "[]")
    except json.JSONDecodeError:
        return (None, row)
    ids = [mid for mid in ids_raw if isinstance(mid, int)]
    row.last_used_at = _utcnow_iso()
    return (ids, row)


def _write_cached_meeting_ids(
    db: Session,
    *,
    from_date: str,
    to_date: str,
    crawl: bool,
    chunk_days: int,
    meeting_ids: list[int],
) -> MeetingRangeDiscoveryCache:
    row = _get_range_discovery_cache(
        db,
        from_date=from_date,
        to_date=to_date,
        crawl=crawl,
        chunk_days=chunk_days,
    )
    if not row:
        row = MeetingRangeDiscoveryCache(
            from_date=from_date,
            to_date=to_date,
            crawl=1 if crawl else 0,
            chunk_days=int(chunk_days),
        )
        db.add(row)

    now = _utcnow_iso()
    row.meeting_ids_json = json.dumps(meeting_ids, ensure_ascii=True)
    row.discovered_count = len(meeting_ids)
    row.last_fetched_at = now
    row.last_used_at = now
    db.flush()
    return row


def ingest_range(
    db: Session,
    from_date: str,
    to_date: str,
    limit: int = 50,
    crawl: bool = True,
    chunk_days: int = 31,
    store_raw: bool = True,
    use_recent_cache: bool = True,
    cache_ttl_minutes: int = 60,
    progress_callback: Callable[[dict], None] | None = None,
):
    cache_hit = False
    discovery_source = "network"
    cached_row: MeetingRangeDiscoveryCache | None = None
    ids: list[int]
    if use_recent_cache:
        cached_ids, cached_row = _read_cached_meeting_ids(
            db,
            from_date=from_date,
            to_date=to_date,
            crawl=crawl,
            chunk_days=chunk_days,
            cache_ttl_minutes=cache_ttl_minutes,
        )
        if cached_ids is not None:
            ids = cached_ids
            cache_hit = True
            discovery_source = "cache"
        else:
            ids = []
    else:
        ids = []

    if not cache_hit:
        if crawl:
            ids = _collect_meeting_ids(from_date=from_date, to_date=to_date, chunk_days=chunk_days)
        else:
            meetings = cw.list_meetings(from_date, to_date)
            ids = []
            seen: set[int] = set()
            for m in meetings:
                mid = m.get("Id")
                if isinstance(mid, int) and mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
        cached_row = _write_cached_meeting_ids(
            db,
            from_date=from_date,
            to_date=to_date,
            crawl=crawl,
            chunk_days=chunk_days,
            meeting_ids=ids,
        )
        db.commit()

    ids = ids[: max(limit, 0)]
    if progress_callback:
        progress_callback(
            {
                "stage": "discovered",
                "discovered": len(ids),
                "processed": 0,
                "current_meeting_id": None,
                "discovery_source": discovery_source,
                "cache_hit": cache_hit,
            }
        )

    results = []
    succeeded = 0
    failed = 0
    for i, mid in enumerate(ids, start=1):
        if progress_callback:
            progress_callback(
                {
                    "stage": "ingesting",
                    "discovered": len(ids),
                    "processed": i - 1,
                    "current_meeting_id": mid,
                    "discovery_source": discovery_source,
                    "cache_hit": cache_hit,
                }
            )
        try:
            result = ingest_meeting(db, mid, store_raw=store_raw)
            results.append(result)
            if result.get("status") in {"ok", "no_agenda_html"}:
                succeeded += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            results.append({"meeting_id": mid, "status": "error", "error": str(exc)})

        if progress_callback:
            progress_callback(
                {
                    "stage": "ingesting",
                    "discovered": len(ids),
                    "processed": i,
                    "current_meeting_id": mid,
                    "discovery_source": discovery_source,
                    "cache_hit": cache_hit,
                }
            )

    if progress_callback:
        progress_callback(
            {
                "stage": "completed",
                "discovered": len(ids),
                "processed": len(ids),
                "current_meeting_id": None,
                "succeeded": succeeded,
                "failed": failed,
                "discovery_source": discovery_source,
                "cache_hit": cache_hit,
            }
        )

    return {
        "from_date": from_date,
        "to_date": to_date,
        "crawl": crawl,
        "chunk_days": chunk_days,
        "use_recent_cache": use_recent_cache,
        "cache_ttl_minutes": cache_ttl_minutes,
        "cache_hit": cache_hit,
        "discovery_source": discovery_source,
        "cache_last_fetched_at": (cached_row.last_fetched_at if cached_row else ""),
        "discovered": len(ids),
        "ingested": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
