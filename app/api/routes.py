from __future__ import annotations

import difflib
import importlib.util
import re
import sys
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, tuple_
from app.config import settings
from app.db import get_db
from app.models import (
    AgendaItem,
    Document,
    DocumentTextExtraction,
    Entity,
    EntityAlias,
    EntityBinding,
    EntityConnection,
    EntityDateValue,
    EntityMention,
    EntityOrganization,
    EntityPerson,
    EntityPlace,
    Meeting,
    MeetingMinutesMetadata,
    MeetingRangeDiscoveryCache,
)
from app.graph import backfill_graph_entities_and_connections
from app.entities import backfill_entity_kind_records
from app.services.civicweb_client import CivicWebClient
from app.classifiers.topics import classify_topics
from app.extractors.zoning import extract_zoning_signals
from app.schemas import (
    AgendaItemOut,
    AgendaTopicSearchOut,
    AddressExploreOut,
    DocumentSearchOut,
    DocumentOut,
    ExplorePopularOut,
    ExploreTopicSummaryOut,
    EntitySuggestOut,
    EntityMentionOut,
    EntityBindingOut,
    EntityConnectionOut,
    ConnectionEvidenceOut,
    RelatedEntityOut,
    EntitySummaryOut,
    MeetingMinutesMetadataOut,
    StoredMeetingSummaryOut,
    PopularTopicOut,
    TimelineBucketOut,
    ZoningSignalsOut,
)
from app.utils.text import normalize_text

router = APIRouter()
client = CivicWebClient(base_url=settings.civicweb_base_url)
KNOWN_CITIES = ["Urbandale", "Des Moines", "Waukee", "Clive", "Windsor Heights", "Johnston"]
ZIP_REGEX = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def _entity_binding_out_rows(db: Session, entity_id: int) -> list[EntityBindingOut]:
    rows = (
        db.query(EntityBinding)
        .filter(EntityBinding.entity_id == entity_id)
        .order_by(EntityBinding.source_table.asc(), EntityBinding.source_id.asc())
        .all()
    )
    return [EntityBindingOut(source_table=row.source_table, source_id=int(row.source_id)) for row in rows]


def _entity_kind_metadata_map(
    db: Session,
    entity: Entity,
    *,
    bindings: list[EntityBindingOut] | None = None,
) -> dict[str, str]:
    etype = (entity.entity_type or "").lower()
    out: dict[str, str] = {}
    binding_rows = bindings if bindings is not None else _entity_binding_out_rows(db, int(entity.id))

    if etype == "person":
        row = db.query(EntityPerson).filter(EntityPerson.entity_id == entity.id).one_or_none()
        if row:
            if row.full_name:
                out["full_name"] = normalize_text(row.full_name)
            if row.first_name:
                out["first_name"] = normalize_text(row.first_name)
            if row.last_name:
                out["last_name"] = normalize_text(row.last_name)
    elif etype in {"address", "zip_code"}:
        row = db.query(EntityPlace).filter(EntityPlace.entity_id == entity.id).one_or_none()
        if row:
            if row.address_text:
                out["address"] = normalize_text(row.address_text)
            if row.city_hint:
                out["city"] = normalize_text(row.city_hint)
            if row.state_hint:
                out["state"] = normalize_text(row.state_hint)
            if row.zip_hint:
                out["zip"] = normalize_text(row.zip_hint)
    elif etype == "organization":
        row = db.query(EntityOrganization).filter(EntityOrganization.entity_id == entity.id).one_or_none()
        if row:
            if row.name_text:
                out["name"] = normalize_text(row.name_text)
            if row.legal_suffix:
                out["suffix"] = normalize_text(row.legal_suffix)
        alias_count = (
            db.query(func.count(EntityAlias.id))
            .filter(EntityAlias.entity_id == entity.id)
            .scalar()
            or 0
        )
        if alias_count:
            out["aliases"] = str(int(alias_count))
    elif etype == "date":
        row = db.query(EntityDateValue).filter(EntityDateValue.entity_id == entity.id).one_or_none()
        if row:
            if row.date_iso:
                out["date_iso"] = normalize_text(row.date_iso)
            if row.label_text:
                out["label"] = normalize_text(row.label_text)
    elif etype == "meeting":
        meeting_binding = next((b for b in binding_rows if b.source_table == "meetings"), None)
        if meeting_binding:
            meeting = db.get(Meeting, int(meeting_binding.source_id))
            if meeting:
                out["meeting_id"] = str(int(meeting.meeting_id))
                if meeting.name:
                    out["name"] = normalize_text(meeting.name)
                if meeting.location:
                    out["location"] = normalize_text(meeting.location)
                if meeting.time:
                    out["time"] = normalize_text(meeting.time)
                if meeting.date:
                    out["date"] = normalize_text(meeting.date)
    elif etype == "document":
        doc_binding = next((b for b in binding_rows if b.source_table == "documents"), None)
        if doc_binding:
            doc = db.get(Document, int(doc_binding.source_id))
            if doc:
                out["meeting_id"] = str(int(doc.meeting_id))
                out["document_id"] = str(int(doc.document_id))
                if doc.title:
                    out["title"] = normalize_text(doc.title)
    return out


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/runtime")
async def runtime_info():
    return {
        "python_executable": sys.executable,
        "pypdf_available": bool(importlib.util.find_spec("pypdf")),
    }


@router.post("/graph/backfill")
def graph_backfill(
    limit: int | None = Query(default=None, ge=1, le=10000),
    meeting_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    graph_stats = backfill_graph_entities_and_connections(db, limit=limit, meeting_id=meeting_id)
    kind_stats = backfill_entity_kind_records(db)
    return {
        **graph_stats,
        "entity_kind_rows_upserted": kind_stats.get("upserted", 0),
    }


@router.get("/ingest/cache-status")
def ingest_cache_status(
    from_date: str,
    to_date: str,
    crawl: bool = True,
    chunk_days: int = 31,
    cache_ttl_minutes: int = 60,
    db: Session = Depends(get_db),
):
    row = (
        db.query(MeetingRangeDiscoveryCache)
        .filter(
            MeetingRangeDiscoveryCache.from_date == from_date,
            MeetingRangeDiscoveryCache.to_date == to_date,
            MeetingRangeDiscoveryCache.crawl == (1 if crawl else 0),
            MeetingRangeDiscoveryCache.chunk_days == int(chunk_days),
        )
        .one_or_none()
    )
    if not row or not row.last_fetched_at:
        return {
            "has_cache": False,
            "cache_fresh": False,
            "discovered_count": 0,
            "last_fetched_at": "",
            "cache_ttl_minutes": cache_ttl_minutes,
        }

    cache_fresh = False
    if row.last_fetched_at:
        try:
            fetched_at = datetime.fromisoformat(row.last_fetched_at.replace("Z", "+00:00"))
            cache_fresh = (
                datetime.now(fetched_at.tzinfo) - fetched_at
            ).total_seconds() <= max(cache_ttl_minutes, 0) * 60
        except ValueError:
            cache_fresh = False

    return {
        "has_cache": True,
        "cache_fresh": cache_fresh,
        "discovered_count": int(row.discovered_count or 0),
        "last_fetched_at": row.last_fetched_at or "",
        "last_used_at": row.last_used_at or "",
        "cache_ttl_minutes": cache_ttl_minutes,
    }


@router.get("/explore/coverage")
def explore_coverage(db: Session = Depends(get_db)):
    meeting_count = db.query(func.count(Meeting.meeting_id)).scalar() or 0
    agenda_count = db.query(func.count(AgendaItem.id)).scalar() or 0
    document_count = db.query(func.count(Document.id)).scalar() or 0
    minutes_count = db.query(func.count(MeetingMinutesMetadata.id)).scalar() or 0
    entity_count = db.query(func.count(Entity.id)).scalar() or 0
    connection_count = db.query(func.count(EntityConnection.id)).scalar() or 0
    entity_type_rows = (
        db.query(Entity.entity_type, func.count(Entity.id))
        .group_by(Entity.entity_type)
        .all()
    )
    entity_type_counts = [
        {"entity_type": (entity_type or "unknown"), "count": int(count or 0)}
        for entity_type, count in sorted(entity_type_rows, key=lambda row: (-int(row[1] or 0), str(row[0] or "")))
    ]
    cache_rows = (
        db.query(MeetingRangeDiscoveryCache)
        .order_by(MeetingRangeDiscoveryCache.last_fetched_at.desc(), MeetingRangeDiscoveryCache.id.desc())
        .limit(8)
        .all()
    )
    recent_ranges = [
        {
            "from_date": r.from_date,
            "to_date": r.to_date,
            "crawl": bool(r.crawl),
            "chunk_days": int(r.chunk_days or 0),
            "discovered_count": int(r.discovered_count or 0),
            "last_fetched_at": r.last_fetched_at or "",
            "last_used_at": r.last_used_at or "",
        }
        for r in cache_rows
    ]
    return {
        "meeting_count": int(meeting_count),
        "agenda_item_count": int(agenda_count),
        "document_count": int(document_count),
        "minutes_metadata_count": int(minutes_count),
        "entity_count": int(entity_count),
        "connection_count": int(connection_count),
        "entity_type_counts": entity_type_counts,
        "recent_discovery_ranges": recent_ranges,
    }


@router.get("/entities/suggest", response_model=list[EntitySuggestOut])
def suggest_entities(
    q: str = Query(..., min_length=1),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    needle = normalize_text(q).lower()
    entity_type_norm = normalize_text(entity_type or "").lower()
    tokens = [t for t in needle.replace(",", " ").split() if t]

    # Pull a manageable candidate pool, then score in Python for loose matches.
    candidates = db.query(Entity).order_by(Entity.id.desc()).limit(5000).all()
    scored: list[tuple[float, Entity]] = []
    for entity in candidates:
        if entity_type_norm and (entity.entity_type or "").lower() != entity_type_norm:
            continue
        text = (entity.display_value or "").lower()
        if not text:
            continue
        ratio = difflib.SequenceMatcher(None, needle, text).ratio()
        starts = 1.0 if text.startswith(needle) else 0.0
        contains = 1.0 if needle in text else 0.0
        token_hits = sum(1 for t in tokens if t in text) / max(len(tokens), 1)
        score = (starts * 2.0) + (contains * 1.5) + (token_hits * 1.2) + ratio
        if score >= 0.4:
            scored.append((score, entity))

    scored.sort(key=lambda x: (-x[0], x[1].display_value.lower()))
    return [
        EntitySuggestOut(
            entity_id=e.id,
            entity_type=e.entity_type,
            display_value=e.display_value,
            score=round(score, 3),
        )
        for score, e in scored[:limit]
    ]


@router.get("/meetings")
async def list_meetings(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query("9999-12-31", description="YYYY-MM-DD"),
):
    meetings = await client.list_meetings(date_from=date_from, date_to=date_to)
    return {"count": len(meetings), "items": meetings}


@router.get("/stored/meetings", response_model=list[StoredMeetingSummaryOut])
def list_stored_meetings(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    q: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    meetings = db.query(Meeting).order_by(Meeting.meeting_id.desc()).limit(5000).all()
    q_norm = normalize_text(q or "").lower()
    q_meeting_id_match = re.search(r"\bmeeting\s+(\d+)\b", q_norm)
    q_meeting_id = int(q_meeting_id_match.group(1)) if q_meeting_id_match else (int(q_norm) if q_norm.isdigit() else None)
    topic_norm = normalize_text(topic or "").lower()
    out: list[StoredMeetingSummaryOut] = []
    for m in meetings:
        # `Meeting.date` may be blank in current ingest; fall back to lexical match against name when date filters provided.
        display_date = m.date or ""
        if date_from and display_date and display_date < date_from:
            continue
        if date_to and display_date and display_date > date_to:
            continue
        hay = " ".join([m.name or "", m.location or "", m.time or ""]).lower()
        q_matches = (not q_norm) or (q_norm in hay) or (q_norm in str(m.meeting_id))
        if q_meeting_id is not None and int(m.meeting_id) == q_meeting_id:
            q_matches = True
        if not q_matches:
            continue

        agenda_items = db.query(AgendaItem).filter(AgendaItem.meeting_id == m.meeting_id).all()
        docs_count = db.query(func.count(Document.id)).filter(Document.meeting_id == m.meeting_id).scalar() or 0
        entity_count = (
            db.query(func.count(func.distinct(EntityMention.entity_id)))
            .filter(EntityMention.meeting_id == m.meeting_id)
            .scalar()
            or 0
        )
        minutes_count = (
            db.query(func.count(MeetingMinutesMetadata.id))
            .filter(MeetingMinutesMetadata.meeting_id == m.meeting_id)
            .scalar()
            or 0
        )
        matched_topic_count = 0
        if topic_norm:
            for item in agenda_items:
                item_topics = classify_topics(normalize_text(item.title or ""))
                if topic_norm in item_topics:
                    matched_topic_count += 1
            if matched_topic_count == 0:
                continue

        out.append(
            StoredMeetingSummaryOut(
                meeting_id=m.meeting_id,
                name=normalize_text(m.name or ""),
                date=display_date,
                time=normalize_text(m.time or ""),
                location=normalize_text(m.location or ""),
                agenda_item_count=len(agenda_items),
                document_count=int(docs_count),
                entity_count=int(entity_count),
                minutes_count=int(minutes_count),
                matched_topic_count=matched_topic_count,
            )
        )
        if len(out) >= limit:
            break
    return out


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


@router.get("/meetings/{meeting_id}/minutes-metadata", response_model=list[MeetingMinutesMetadataOut])
def get_minutes_metadata(meeting_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(MeetingMinutesMetadata)
        .filter(MeetingMinutesMetadata.meeting_id == meeting_id)
        .order_by(MeetingMinutesMetadata.document_id.asc())
        .all()
    )

    return [
        MeetingMinutesMetadataOut(
            meeting_id=r.meeting_id,
            document_id=r.document_id,
            title=normalize_text(r.title),
            url=r.url,
            detected_date=r.detected_date or "",
            page_count=r.page_count,
            text_excerpt=normalize_text(r.text_excerpt),
            status=r.status or "unknown",
        )
        for r in rows
    ]


@router.get("/meetings/{meeting_id}/entities", response_model=list[EntitySummaryOut])
def get_meeting_entities(
    meeting_id: int,
    entity_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    mention_query = (
        db.query(EntityMention, Entity)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .filter(EntityMention.meeting_id == meeting_id)
    )

    if entity_type:
        mention_query = mention_query.filter(Entity.entity_type == normalize_text(entity_type).lower())
    if q:
        term = f"%{normalize_text(q).lower()}%"
        mention_query = mention_query.filter(
            func.lower(Entity.display_value).like(term) | func.lower(EntityMention.mention_text).like(term)
        )

    rows = mention_query.order_by(Entity.entity_type.asc(), Entity.display_value.asc()).all()

    grouped: dict[int, EntitySummaryOut] = {}
    for mention, entity in rows:
        if entity.id not in grouped:
            if len(grouped) >= limit:
                continue
            bindings = _entity_binding_out_rows(db, entity.id)
            grouped[entity.id] = EntitySummaryOut(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                display_value=entity.display_value,
                normalized_value=entity.normalized_value,
                mention_count=0,
                kind_metadata=_entity_kind_metadata_map(db, entity, bindings=bindings),
                bindings=bindings,
                mentions=[],
            )
        summary = grouped[entity.id]
        summary.mention_count += 1
        summary.mentions.append(
            EntityMentionOut(
                meeting_id=mention.meeting_id,
                source_type=mention.source_type,
                source_id=mention.source_id,
                agenda_item_id=mention.agenda_item_id,
                document_id=mention.document_id,
                mention_text=normalize_text(mention.mention_text),
                context_text=normalize_text(mention.context_text),
                confidence=float(mention.confidence or 0.0),
            )
        )

    return list(grouped.values())


@router.get("/entities/search", response_model=list[EntitySummaryOut])
def search_entities(
    q: str = Query(..., min_length=1),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    term = f"%{normalize_text(q).lower()}%"
    query = db.query(Entity).filter(func.lower(Entity.display_value).like(term))
    if entity_type:
        query = query.filter(Entity.entity_type == normalize_text(entity_type).lower())
    entities = query.order_by(Entity.entity_type.asc(), Entity.display_value.asc()).limit(limit).all()

    out: list[EntitySummaryOut] = []
    for entity in entities:
        bindings = _entity_binding_out_rows(db, entity.id)
        total_mentions = (
            db.query(func.count(EntityMention.id))
            .filter(EntityMention.entity_id == entity.id)
            .scalar()
            or 0
        )
        mentions = (
            db.query(EntityMention)
            .filter(EntityMention.entity_id == entity.id)
            .order_by(EntityMention.meeting_id.desc(), EntityMention.id.desc())
            .limit(5)
            .all()
        )
        out.append(
            EntitySummaryOut(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                display_value=entity.display_value,
                normalized_value=entity.normalized_value,
                mention_count=int(total_mentions),
                kind_metadata=_entity_kind_metadata_map(db, entity, bindings=bindings),
                bindings=bindings,
                mentions=[
                    EntityMentionOut(
                        meeting_id=m.meeting_id,
                        source_type=m.source_type,
                        source_id=m.source_id,
                        agenda_item_id=m.agenda_item_id,
                        document_id=m.document_id,
                        mention_text=normalize_text(m.mention_text),
                        context_text=normalize_text(m.context_text),
                        confidence=float(m.confidence or 0.0),
                    )
                    for m in mentions
                ],
            )
        )
    return out


@router.get("/entities/{entity_id}", response_model=EntitySummaryOut)
def get_entity_detail(
    entity_id: int,
    mention_limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    entity = db.query(Entity).filter(Entity.id == entity_id).one_or_none()
    if not entity:
        return EntitySummaryOut(
            entity_id=entity_id,
            entity_type="unknown",
            display_value="",
            normalized_value="",
            mention_count=0,
            kind_metadata={},
            bindings=[],
            mentions=[],
        )
    total_mentions = (
        db.query(func.count(EntityMention.id))
        .filter(EntityMention.entity_id == entity.id)
        .scalar()
        or 0
    )
    mentions = (
        db.query(EntityMention)
        .filter(EntityMention.entity_id == entity.id)
        .order_by(EntityMention.meeting_id.desc(), EntityMention.id.desc())
        .limit(mention_limit)
        .all()
    )
    bindings = _entity_binding_out_rows(db, entity.id)
    return EntitySummaryOut(
        entity_id=entity.id,
        entity_type=entity.entity_type,
        display_value=entity.display_value,
        normalized_value=entity.normalized_value,
        mention_count=int(total_mentions),
        kind_metadata=_entity_kind_metadata_map(db, entity, bindings=bindings),
        bindings=bindings,
        mentions=[
            EntityMentionOut(
                meeting_id=m.meeting_id,
                source_type=m.source_type,
                source_id=m.source_id,
                agenda_item_id=m.agenda_item_id,
                document_id=m.document_id,
                mention_text=normalize_text(m.mention_text),
                context_text=normalize_text(m.context_text),
                confidence=float(m.confidence or 0.0),
            )
            for m in mentions
        ],
    )


@router.get("/entities/{entity_id}/related", response_model=list[RelatedEntityOut])
def related_entities(
    entity_id: int,
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    target = db.query(Entity).filter(Entity.id == entity_id).one_or_none()
    if not target:
        return []

    target_meeting_ids = [
        row[0]
        for row in (
            db.query(EntityMention.meeting_id)
            .filter(EntityMention.entity_id == entity_id)
            .distinct()
            .all()
        )
    ]
    if not target_meeting_ids:
        return []

    rows = (
        db.query(EntityMention, Entity)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .filter(EntityMention.meeting_id.in_(target_meeting_ids))
        .filter(EntityMention.entity_id != entity_id)
        .all()
    )

    counts: dict[int, dict] = {}
    for mention, entity in rows:
        bucket = counts.setdefault(
            entity.id,
            {
                "entity": entity,
                "cooccurrence_count": 0,
                "meeting_ids": set(),
            },
        )
        bucket["cooccurrence_count"] += 1
        bucket["meeting_ids"].add(mention.meeting_id)

    ranked = sorted(
        counts.values(),
        key=lambda b: (-b["shared_meeting_count"] if "shared_meeting_count" in b else -len(b["meeting_ids"]), -b["cooccurrence_count"], b["entity"].display_value.lower()),
    )

    out: list[RelatedEntityOut] = []
    for bucket in ranked[:limit]:
        entity = bucket["entity"]
        out.append(
            RelatedEntityOut(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                display_value=entity.display_value,
                normalized_value=entity.normalized_value,
                cooccurrence_count=int(bucket["cooccurrence_count"]),
                shared_meeting_count=len(bucket["meeting_ids"]),
            )
        )
    return out


@router.get("/entities/{entity_id}/connections", response_model=list[EntityConnectionOut])
def get_entity_connections(
    entity_id: int,
    topic: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    target = db.query(Entity).filter(Entity.id == entity_id).one_or_none()
    if not target:
        return []

    edges = (
        db.query(EntityConnection)
        .filter(
            (EntityConnection.from_entity_id == entity_id) | (EntityConnection.to_entity_id == entity_id)
        )
        .order_by(EntityConnection.id.desc())
        .limit(5000)
        .all()
    )
    if not edges:
        return []

    topic_norm = normalize_text(topic or "")
    if topic_norm:
        source_topic_cache: dict[tuple[str, int], bool] = {}
        mention_source_cache: dict[tuple[str, int], EntityMention | None] = {}
        agenda_cache: dict[int, AgendaItem | None] = {}
        doc_cache: dict[int, Document | None] = {}
        extraction_cache: dict[int, DocumentTextExtraction | None] = {}
        minutes_cache: dict[int, MeetingMinutesMetadata | None] = {}
        meeting_cache: dict[int, Meeting | None] = {}

        def _agenda_row(row_id: int) -> AgendaItem | None:
            if row_id not in agenda_cache:
                agenda_cache[row_id] = db.get(AgendaItem, row_id)
            return agenda_cache[row_id]

        def _doc_row(row_id: int) -> Document | None:
            if row_id not in doc_cache:
                doc_cache[row_id] = db.get(Document, row_id)
            return doc_cache[row_id]

        def _extraction_row(row_id: int) -> DocumentTextExtraction | None:
            if row_id not in extraction_cache:
                extraction_cache[row_id] = db.get(DocumentTextExtraction, row_id)
            return extraction_cache[row_id]

        def _minutes_row(row_id: int) -> MeetingMinutesMetadata | None:
            if row_id not in minutes_cache:
                minutes_cache[row_id] = db.get(MeetingMinutesMetadata, row_id)
            return minutes_cache[row_id]

        def _meeting_row(row_id: int) -> Meeting | None:
            if row_id not in meeting_cache:
                meeting_cache[row_id] = db.get(Meeting, row_id)
            return meeting_cache[row_id]

        def _mention_source_row(source_type: str, source_id: int) -> EntityMention | None:
            key = (source_type, source_id)
            if key not in mention_source_cache:
                mention_source_cache[key] = (
                    db.query(EntityMention)
                    .filter(
                        EntityMention.source_type == source_type,
                        EntityMention.source_id == source_id,
                    )
                    .order_by(EntityMention.id.desc())
                    .first()
                )
            return mention_source_cache[key]

        def _edge_matches_topic(edge: EntityConnection) -> bool:
            source_type = normalize_text(edge.evidence_source_type or "")
            source_id = int(edge.evidence_source_id or 0)
            cache_key = (source_type, source_id)
            if cache_key in source_topic_cache:
                return source_topic_cache[cache_key]

            text_parts: list[str] = []
            if source_type and source_id > 0 and source_type != "documents":
                mention = _mention_source_row(source_type, source_id)
                if mention:
                    text_parts.extend([mention.context_text or "", mention.mention_text or ""])
            if source_type in {"agenda_item_title", "agenda_items"} and source_id > 0:
                agenda = _agenda_row(source_id)
                if agenda:
                    text_parts.extend([agenda.title or "", agenda.section or "", agenda.item_key or ""])
            elif source_type in {"document_title", "documents"} and source_id > 0:
                doc = _doc_row(source_id)
                if doc:
                    text_parts.append(doc.title or "")
                    if doc.agenda_item_id:
                        agenda = _agenda_row(int(doc.agenda_item_id))
                        if agenda:
                            text_parts.append(agenda.title or "")
            elif source_type == "document_content" and source_id > 0:
                extraction = _extraction_row(source_id)
                if extraction:
                    text_parts.extend([extraction.title or "", extraction.text_excerpt or ""])
            elif source_type == "minutes_excerpt" and source_id > 0:
                minutes = _minutes_row(source_id)
                if minutes:
                    text_parts.extend([minutes.title or "", minutes.text_excerpt or ""])
            elif source_type == "meeting_metadata" and source_id > 0:
                meeting = _meeting_row(source_id)
                if meeting:
                    text_parts.extend(
                        [
                            meeting.name or "",
                            meeting.location or "",
                            meeting.date or "",
                            meeting.time or "",
                        ]
                    )

            source_text = normalize_text(" ".join(p for p in text_parts if p).strip())
            if not source_text:
                source_topic_cache[cache_key] = False
                return False
            source_topic_cache[cache_key] = topic_norm in classify_topics(source_text)
            return source_topic_cache[cache_key]

        edges = [edge for edge in edges if _edge_matches_topic(edge)]
        if not edges:
            return []

    buckets: dict[tuple[int, str, str], dict] = {}
    other_ids: set[int] = set()
    for edge in edges:
        outgoing = int(edge.from_entity_id) == int(entity_id)
        other_id = int(edge.to_entity_id if outgoing else edge.from_entity_id)
        other_ids.add(other_id)
        direction = "outgoing" if outgoing else "incoming"
        key = (other_id, edge.relation_type or "", direction)
        bucket = buckets.setdefault(
            key,
            {
                "edge_count": 0,
                "evidence_count": 0,
                "meeting_ids": set(),
            },
        )
        bucket["edge_count"] += 1
        bucket["evidence_count"] += max(1, int(edge.evidence_count or 1))
        if edge.meeting_id is not None:
            bucket["meeting_ids"].add(int(edge.meeting_id))

    if not buckets:
        return []

    entities = {
        row.id: row
        for row in db.query(Entity).filter(Entity.id.in_(list(other_ids))).all()
    }

    entity_type_norm = normalize_text(entity_type or "")
    candidate_keys = [
        key
        for key in buckets.keys()
        if not entity_type_norm
        or (
            (other := entities.get(key[0])) is not None
            and normalize_text(other.entity_type or "") == entity_type_norm
        )
    ]
    if not candidate_keys:
        return []

    ranked_keys = sorted(
        candidate_keys,
        key=lambda key: (
            -len(buckets[key]["meeting_ids"]),
            -int(buckets[key]["evidence_count"]),
            -int(buckets[key]["edge_count"]),
            (entities.get(key[0]).display_value.lower() if entities.get(key[0]) else ""),
        ),
    )
    out: list[EntityConnectionOut] = []
    for key in ranked_keys[:limit]:
        other_id, relation_type, direction = key
        other = entities.get(other_id)
        if not other:
            continue
        bucket = buckets[key]
        bindings = _entity_binding_out_rows(db, other.id)
        out.append(
            EntityConnectionOut(
                entity_id=other.id,
                entity_type=other.entity_type,
                display_value=other.display_value,
                normalized_value=other.normalized_value,
                relation_type=relation_type,
                direction=direction,
                edge_count=int(bucket["edge_count"]),
                evidence_count=int(bucket["evidence_count"]),
                shared_meeting_count=len(bucket["meeting_ids"]),
                kind_metadata=_entity_kind_metadata_map(db, other, bindings=bindings),
                bindings=bindings,
            )
        )
    return out


@router.get("/entities/{entity_id}/connections/{other_entity_id}/evidence", response_model=list[ConnectionEvidenceOut])
def get_entity_connection_evidence(
    entity_id: int,
    other_entity_id: int,
    relation_type: str = Query(..., min_length=1),
    direction: str = Query(..., pattern="^(incoming|outgoing)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    relation_norm = normalize_text(relation_type)
    if direction == "outgoing":
        from_id, to_id = entity_id, other_entity_id
    else:
        from_id, to_id = other_entity_id, entity_id

    edges = (
        db.query(EntityConnection)
        .filter(
            EntityConnection.from_entity_id == from_id,
            EntityConnection.to_entity_id == to_id,
            EntityConnection.relation_type == relation_norm,
        )
        .order_by(EntityConnection.id.desc())
        .limit(limit)
        .all()
    )
    if not edges:
        return []

    mention_keys = {
        (e.evidence_source_type or "", int(e.evidence_source_id or 0))
        for e in edges
        if (e.evidence_source_type or "") not in {"documents"}
    }
    mention_lookup: dict[tuple[str, int], EntityMention] = {}
    if mention_keys:
        mention_rows = (
            db.query(EntityMention)
            .filter(
                tuple_(EntityMention.source_type, EntityMention.source_id).in_(list(mention_keys))
            )
            .all()
        )
        for m in mention_rows:
            mention_lookup[(m.source_type or "", int(m.source_id or 0))] = m

    document_ids = [
        int(e.evidence_source_id)
        for e in edges
        if (e.evidence_source_type or "") == "documents" and int(e.evidence_source_id or 0) > 0
    ]
    document_lookup: dict[int, Document] = {}
    if document_ids:
        for d in db.query(Document).filter(Document.id.in_(document_ids)).all():
            document_lookup[int(d.id)] = d

    out: list[ConnectionEvidenceOut] = []
    for edge in edges:
        mention = mention_lookup.get((edge.evidence_source_type or "", int(edge.evidence_source_id or 0)))
        doc = document_lookup.get(int(edge.evidence_source_id or 0)) if (edge.evidence_source_type or "") == "documents" else None
        out.append(
            ConnectionEvidenceOut(
                relation_type=edge.relation_type or "",
                direction=direction,
                meeting_id=edge.meeting_id,
                document_id=edge.document_id,
                evidence_source_type=edge.evidence_source_type or "",
                evidence_source_id=int(edge.evidence_source_id or 0),
                strength=float(edge.strength or 0.0),
                mention_text=normalize_text((mention.mention_text if mention else "") or ""),
                context_text=normalize_text(
                    (mention.context_text if mention else "") or (doc.title if doc else "")
                ),
            )
        )
    return out


@router.get("/search/content")
def search_content(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    term = f"%{normalize_text(q).lower()}%"

    agenda_rows = (
        db.query(AgendaItem)
        .filter(func.lower(AgendaItem.title).like(term))
        .order_by(AgendaItem.meeting_id.desc(), AgendaItem.item_key.asc())
        .limit(limit)
        .all()
    )
    document_rows = (
        db.query(Document)
        .filter(func.lower(Document.title).like(term))
        .order_by(Document.meeting_id.desc(), Document.document_id.desc())
        .limit(limit)
        .all()
    )

    return {
        "agenda_topics": [
            AgendaTopicSearchOut(
                meeting_id=row.meeting_id,
                agenda_item_id=row.id,
                item_key=row.item_key,
                title=normalize_text(row.title),
                section=row.section or "",
            )
            for row in agenda_rows
        ],
        "documents": [
            DocumentSearchOut(
                meeting_id=row.meeting_id,
                document_id=row.document_id,
                agenda_item_id=row.agenda_item_id,
                title=normalize_text(row.title),
                url=row.url,
            )
            for row in document_rows
        ],
    }


@router.get("/explore/popular", response_model=ExplorePopularOut)
def explore_popular(
    db: Session = Depends(get_db),
    entity_limit: int = Query(default=12, ge=1, le=100),
    topic_limit: int = Query(default=12, ge=1, le=100),
):
    top_entities = (
        db.query(Entity, func.count(EntityMention.id))
        .join(EntityMention, EntityMention.entity_id == Entity.id)
        .group_by(Entity.id)
        .order_by(func.count(EntityMention.id).desc(), Entity.display_value.asc())
        .limit(entity_limit)
        .all()
    )

    entities_out: list[EntitySummaryOut] = []
    for entity, count in top_entities:
        bindings = _entity_binding_out_rows(db, entity.id)
        mentions = (
            db.query(EntityMention)
            .filter(EntityMention.entity_id == entity.id)
            .order_by(EntityMention.meeting_id.desc(), EntityMention.id.desc())
            .limit(3)
            .all()
        )
        entities_out.append(
            EntitySummaryOut(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                display_value=entity.display_value,
                normalized_value=entity.normalized_value,
                mention_count=int(count or 0),
                kind_metadata=_entity_kind_metadata_map(db, entity, bindings=bindings),
                bindings=bindings,
                mentions=[
                    EntityMentionOut(
                        meeting_id=m.meeting_id,
                        source_type=m.source_type,
                        source_id=m.source_id,
                        agenda_item_id=m.agenda_item_id,
                        document_id=m.document_id,
                        mention_text=normalize_text(m.mention_text),
                        context_text=normalize_text(m.context_text),
                        confidence=float(m.confidence or 0.0),
                    )
                    for m in mentions
                ],
            )
        )

    topic_counts: dict[str, int] = {}
    for row in db.query(AgendaItem).order_by(AgendaItem.id.desc()).limit(5000).all():
        for topic in classify_topics(row.title or ""):
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    topics_out = [
        PopularTopicOut(topic=t, count=c)
        for t, c in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:topic_limit]
    ]
    return ExplorePopularOut(topics=topics_out, entities=entities_out)


@router.get("/explore/topics", response_model=list[ExploreTopicSummaryOut])
def explore_topics(
    q: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q_norm = normalize_text(q or "").lower()
    buckets: dict[str, dict[str, object]] = {}
    rows = db.query(AgendaItem).order_by(AgendaItem.meeting_id.desc(), AgendaItem.id.desc()).limit(10000).all()
    for row in rows:
        title = normalize_text(row.title or "")
        topics = classify_topics(title)
        for topic in topics:
            if q_norm and q_norm not in topic:
                continue
            b = buckets.setdefault(topic, {"agenda_item_count": 0, "meeting_ids": []})
            b["agenda_item_count"] = int(b["agenda_item_count"]) + 1
            mids = b["meeting_ids"]
            if row.meeting_id not in mids:
                mids.append(row.meeting_id)

    ranked = sorted(
        buckets.items(),
        key=lambda kv: (-int(kv[1]["agenda_item_count"]), kv[0]),
    )[:limit]
    return [
        ExploreTopicSummaryOut(
            topic=topic,
            agenda_item_count=int(data["agenda_item_count"]),
            meeting_count=len(data["meeting_ids"]),
            recent_meeting_ids=list(data["meeting_ids"])[:5],
        )
        for topic, data in ranked
    ]


@router.get("/explore/timeline", response_model=list[TimelineBucketOut])
def explore_timeline(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Entity, EntityMention)
        .join(EntityMention, EntityMention.entity_id == Entity.id)
        .filter(Entity.entity_type == "date")
    )
    if q:
        term = f"%{normalize_text(q).lower()}%"
        query = query.filter(func.lower(Entity.display_value).like(term) | func.lower(Entity.normalized_value).like(term))
    rows = query.all()

    buckets: dict[str, dict] = {}
    for entity, mention in rows:
        key = entity.normalized_value or entity.display_value
        if not key:
            continue
        b = buckets.setdefault(
            key,
            {
                "entity_id": int(entity.id),
                "date": key,
                "label": entity.display_value or key,
                "meeting_ids": set(),
                "entity_count": 0,
            },
        )
        b["meeting_ids"].add(mention.meeting_id)
        b["entity_count"] += 1

    ranked = sorted(
        buckets.values(),
        key=lambda b: (b["date"], b["label"]),
        reverse=True,
    )[:limit]
    return [
        TimelineBucketOut(
            entity_id=int(b["entity_id"]),
            date=str(b["date"]),
            label=str(b["label"]),
            meeting_ids=sorted(list(b["meeting_ids"]), reverse=True),
            entity_count=int(b["entity_count"]),
        )
        for b in ranked
    ]


@router.get("/explore/locations", response_model=list[AddressExploreOut])
def explore_locations(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Entity, EntityMention)
        .join(EntityMention, EntityMention.entity_id == Entity.id)
        .filter(Entity.entity_type == "address")
    )
    if q:
        term = f"%{normalize_text(q).lower()}%"
        query = query.filter(func.lower(Entity.display_value).like(term) | func.lower(Entity.normalized_value).like(term))
    rows = query.all()

    buckets: dict[int, dict] = {}
    for entity, mention in rows:
        context = normalize_text(mention.context_text)
        b = buckets.setdefault(
            entity.id,
            {
                "entity_id": entity.id,
                "address": entity.display_value,
                "meeting_ids": set(),
                "mention_count": 0,
                "cities": {},
                "state_hint": "Iowa" if "iowa" in context.lower() else "",
                "zips": {},
            },
        )
        b["meeting_ids"].add(mention.meeting_id)
        b["mention_count"] += 1
        for city in KNOWN_CITIES:
            if city.lower() in context.lower():
                b["cities"][city] = b["cities"].get(city, 0) + 1
        if not b["state_hint"] and "iowa" in context.lower():
            b["state_hint"] = "Iowa"
        for z in ZIP_REGEX.findall(context):
            b["zips"][z] = b["zips"].get(z, 0) + 1

    ranked = sorted(
        buckets.values(),
        key=lambda b: (-len(b["meeting_ids"]), -b["mention_count"], b["address"].lower()),
    )[:limit]
    return [
        # Prefer contextual city from mentions; default to Urbandale/Iowa for this deployment.
        AddressExploreOut(
            entity_id=int(b["entity_id"]),
            address=str(b["address"]),
            city_hint=(sorted(b["cities"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if b["cities"] else "Urbandale"),
            state_hint=(b["state_hint"] or "Iowa"),
            zip_hint=(sorted(b["zips"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if b["zips"] else ""),
            map_query=(
                f"{b['address']}, "
                f"{(sorted(b['cities'].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if b['cities'] else 'Urbandale')}, "
                f"{(b['state_hint'] or 'Iowa')}"
                + (f" {(sorted(b['zips'].items(), key=lambda kv: (-kv[1], kv[0]))[0][0])}" if b["zips"] else "")
            ),
            shared_meeting_count=len(b["meeting_ids"]),
            mention_count=int(b["mention_count"]),
        )
        for b in ranked
    ]
