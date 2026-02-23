from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Document, Entity, EntityBinding, EntityConnection, EntityMention, Meeting
from .utils.text import normalize_text


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _upsert_entity_node(db: Session, *, entity_type: str, display_value: str, normalized_value: str) -> Entity:
    row = (
        db.query(Entity)
        .filter(Entity.entity_type == entity_type, Entity.normalized_value == normalized_value)
        .one_or_none()
    )
    if not row:
        row = Entity(
            entity_type=entity_type,
            display_value=normalize_text(display_value),
            normalized_value=normalized_value,
        )
        db.add(row)
        db.flush()
    elif display_value and (not row.display_value or row.display_value != display_value):
        row.display_value = normalize_text(display_value)
    return row


def upsert_entity_binding(
    db: Session,
    *,
    entity_id: int,
    source_table: str,
    source_id: int,
) -> EntityBinding:
    row = (
        db.query(EntityBinding)
        .filter(EntityBinding.source_table == source_table, EntityBinding.source_id == int(source_id))
        .one_or_none()
    )
    if not row:
        row = EntityBinding(entity_id=entity_id, source_table=source_table, source_id=int(source_id))
        db.add(row)
        db.flush()
    elif row.entity_id != entity_id:
        row.entity_id = entity_id
    return row


def upsert_entity_connection(
    db: Session,
    *,
    from_entity_id: int,
    to_entity_id: int,
    relation_type: str,
    meeting_id: int | None,
    document_id: int | None,
    evidence_source_type: str,
    evidence_source_id: int,
    strength: float = 1.0,
) -> EntityConnection:
    row = (
        db.query(EntityConnection)
        .filter(
            EntityConnection.from_entity_id == int(from_entity_id),
            EntityConnection.to_entity_id == int(to_entity_id),
            EntityConnection.relation_type == relation_type,
            EntityConnection.evidence_source_type == evidence_source_type,
            EntityConnection.evidence_source_id == int(evidence_source_id),
        )
        .one_or_none()
    )
    if not row:
        row = EntityConnection(
            from_entity_id=int(from_entity_id),
            to_entity_id=int(to_entity_id),
            relation_type=relation_type,
            meeting_id=int(meeting_id) if meeting_id is not None else None,
            document_id=int(document_id) if document_id is not None else None,
            evidence_source_type=evidence_source_type,
            evidence_source_id=int(evidence_source_id),
            strength=float(strength),
            evidence_count=1,
            last_seen_at=_utcnow_iso(),
        )
        db.add(row)
        db.flush()
        return row

    row.meeting_id = int(meeting_id) if meeting_id is not None else row.meeting_id
    row.document_id = int(document_id) if document_id is not None else row.document_id
    row.strength = float(strength or row.strength or 1.0)
    row.evidence_count = max(1, int(row.evidence_count or 1))
    row.last_seen_at = _utcnow_iso()
    return row


def _meeting_entity_values(meeting: Meeting) -> tuple[str, str]:
    label = f"Meeting {meeting.meeting_id}"
    if (meeting.name or "").strip():
        label += f" • {normalize_text(meeting.name)}"
    return label, f"meeting:{int(meeting.meeting_id)}"


def _document_entity_values(doc: Document) -> tuple[str, str]:
    title = normalize_text(doc.title or "")
    label = title or f"Document {int(doc.document_id)}"
    return label, f"document:{int(doc.meeting_id)}:{int(doc.document_id)}"


def ensure_meeting_entity(db: Session, meeting: Meeting) -> Entity:
    display_value, normalized_value = _meeting_entity_values(meeting)
    entity = _upsert_entity_node(
        db,
        entity_type="meeting",
        display_value=display_value,
        normalized_value=normalized_value,
    )
    upsert_entity_binding(db, entity_id=entity.id, source_table="meetings", source_id=int(meeting.meeting_id))
    return entity


def ensure_document_entity(db: Session, doc: Document) -> Entity:
    display_value, normalized_value = _document_entity_values(doc)
    entity = _upsert_entity_node(
        db,
        entity_type="document",
        display_value=display_value,
        normalized_value=normalized_value,
    )
    # Bind to local documents.id PK (not CivicWeb document_id) to keep bindings aligned to source table PK.
    upsert_entity_binding(db, entity_id=entity.id, source_table="documents", source_id=int(doc.id))
    return entity


def _meeting_relation_for_mention(mention: EntityMention, mentioned_entity: Entity) -> str:
    if mention.source_type == "meeting_metadata":
        if mentioned_entity.entity_type == "date":
            return "occurs_on"
        if mentioned_entity.entity_type in {"address", "zip_code"}:
            return "occurs_at"
    return "mentions"


def rebuild_graph_for_meeting(db: Session, meeting_id: int) -> dict[str, int]:
    meeting = db.get(Meeting, int(meeting_id))
    if not meeting:
        return {"meeting_entities": 0, "document_entities": 0, "connections": 0}

    db.flush()
    meeting_entity = ensure_meeting_entity(db, meeting)
    connection_count = 0

    docs = (
        db.query(Document)
        .filter(Document.meeting_id == meeting.meeting_id)
        .order_by(Document.id.asc())
        .all()
    )
    doc_entity_by_civic_doc_id: dict[int, int] = {}
    for doc in docs:
        if getattr(doc, "id", None) is None:
            db.flush()
        doc_entity = ensure_document_entity(db, doc)
        doc_entity_by_civic_doc_id[int(doc.document_id)] = int(doc_entity.id)
        upsert_entity_connection(
            db,
            from_entity_id=meeting_entity.id,
            to_entity_id=doc_entity.id,
            relation_type="contains_document",
            meeting_id=meeting.meeting_id,
            document_id=doc.document_id,
            evidence_source_type="documents",
            evidence_source_id=int(doc.id),
            strength=1.0,
        )
        connection_count += 1

    mentions = (
        db.query(EntityMention)
        .filter(EntityMention.meeting_id == meeting.meeting_id)
        .order_by(EntityMention.id.asc())
        .all()
    )
    for mention in mentions:
        mentioned_entity = db.get(Entity, int(mention.entity_id))
        if not mentioned_entity:
            continue

        # Meeting-level relation for any mention in the meeting.
        upsert_entity_connection(
            db,
            from_entity_id=meeting_entity.id,
            to_entity_id=mentioned_entity.id,
            relation_type=_meeting_relation_for_mention(mention, mentioned_entity),
            meeting_id=meeting.meeting_id,
            document_id=mention.document_id,
            evidence_source_type=mention.source_type or "unknown",
            evidence_source_id=int(mention.source_id or 0),
            strength=float(mention.confidence or 1.0),
        )
        connection_count += 1

        # Document-level relation if evidence is attached to a document.
        if mention.document_id is not None:
            doc_entity_id = doc_entity_by_civic_doc_id.get(int(mention.document_id))
            if doc_entity_id:
                upsert_entity_connection(
                    db,
                    from_entity_id=doc_entity_id,
                    to_entity_id=mentioned_entity.id,
                    relation_type="mentions",
                    meeting_id=meeting.meeting_id,
                    document_id=mention.document_id,
                    evidence_source_type=mention.source_type or "unknown",
                    evidence_source_id=int(mention.source_id or 0),
                    strength=float(mention.confidence or 1.0),
                )
                connection_count += 1

    return {
        "meeting_entities": 1,
        "document_entities": len(docs),
        "connections": connection_count,
    }


def backfill_graph_entities_and_connections(
    db: Session,
    *,
    limit: int | None = None,
    meeting_id: int | None = None,
) -> dict[str, int]:
    q = db.query(Meeting).order_by(Meeting.meeting_id.asc())
    if meeting_id is not None:
        q = q.filter(Meeting.meeting_id == int(meeting_id))
    if limit is not None and int(limit) > 0:
        q = q.limit(int(limit))
    meetings = q.all()

    processed = 0
    total_connections = 0
    total_documents = 0
    for m in meetings:
        stats = rebuild_graph_for_meeting(db, m.meeting_id)
        processed += 1
        total_connections += int(stats.get("connections") or 0)
        total_documents += int(stats.get("document_entities") or 0)
    db.commit()
    return {
        "processed_meetings": processed,
        "document_entities_seen": total_documents,
        "connections_written": total_connections,
    }

