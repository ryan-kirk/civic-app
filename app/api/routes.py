from __future__ import annotations

import difflib
import importlib.util
import re
import sys

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.config import settings
from app.db import get_db
from app.models import AgendaItem, Document, Entity, EntityMention, MeetingMinutesMetadata
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
    EntitySuggestOut,
    EntityMentionOut,
    RelatedEntityOut,
    EntitySummaryOut,
    MeetingMinutesMetadataOut,
    PopularTopicOut,
    TimelineBucketOut,
    ZoningSignalsOut,
)
from app.utils.text import normalize_text

router = APIRouter()
client = CivicWebClient(base_url=settings.civicweb_base_url)
KNOWN_CITIES = ["Urbandale", "Des Moines", "Waukee", "Clive", "Windsor Heights", "Johnston"]
ZIP_REGEX = re.compile(r"\b\d{5}(?:-\d{4})?\b")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/runtime")
async def runtime_info():
    return {
        "python_executable": sys.executable,
        "pypdf_available": bool(importlib.util.find_spec("pypdf")),
    }


@router.get("/entities/suggest", response_model=list[EntitySuggestOut])
def suggest_entities(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    needle = normalize_text(q).lower()
    tokens = [t for t in needle.replace(",", " ").split() if t]

    # Pull a manageable candidate pool, then score in Python for loose matches.
    candidates = db.query(Entity).order_by(Entity.id.desc()).limit(5000).all()
    scored: list[tuple[float, Entity]] = []
    for entity in candidates:
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
            grouped[entity.id] = EntitySummaryOut(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                display_value=entity.display_value,
                normalized_value=entity.normalized_value,
                mention_count=0,
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
    return EntitySummaryOut(
        entity_id=entity.id,
        entity_type=entity.entity_type,
        display_value=entity.display_value,
        normalized_value=entity.normalized_value,
        mention_count=int(total_mentions),
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
            {"date": key, "label": entity.display_value or key, "meeting_ids": set(), "entity_count": 0},
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
