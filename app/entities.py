import re
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Entity, EntityAlias, EntityMention
from .utils.text import normalize_text

DATE_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Terrace|Ter|Place|Pl|Circle|Cir|Parkway|Pkwy)\b",
    re.IGNORECASE,
)
ORDINANCE_PATTERN = re.compile(r"\bOrdinance\s+(?:No\.?\s*)?([A-Z]?\d{4}-\d{2,4})\b", re.IGNORECASE)
RESOLUTION_PATTERN = re.compile(r"\bResolution\s+([A-Z]?\d{2,4}-\d{4})\b", re.IGNORECASE)
ORG_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9&'.,-]*(?:\s+[A-Z][A-Za-z0-9&'.,-]*){0,7}\s+(?:LLC|Inc\.?|Company|Corp\.?|Corporation))\b"
)
PERSON_TITLED_PATTERN = re.compile(
    r"\b(?:Mayor|Council\s*Member|Councilmember|Chair|Commissioner|City\s+Manager|Director)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2})\b"
)


def _normalize_entity_value(entity_type: str, value: str) -> tuple[str, str]:
    raw = normalize_text(value)
    if entity_type == "date":
        try:
            return raw, datetime.strptime(raw, "%B %d, %Y").date().isoformat()
        except ValueError:
            return raw, raw.lower()
    if entity_type in {"ordinance_number", "resolution_number"}:
        return raw, raw.upper()
    if entity_type == "address":
        return raw, raw.lower()
    if entity_type == "organization":
        return raw, re.sub(r"\s+", " ", raw).strip().lower()
    if entity_type == "person":
        return raw, re.sub(r"\s+", " ", raw).strip().lower()
    return raw, raw.lower()


def extract_entities_from_text(text: str) -> list[dict[str, str]]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(entity_type: str, match_text: str):
        display, normalized_value = _normalize_entity_value(entity_type, match_text)
        key = (entity_type, normalized_value)
        if key in seen:
            return
        seen.add(key)
        found.append(
            {
                "entity_type": entity_type,
                "display_value": display,
                "normalized_value": normalized_value,
                "mention_text": normalize_text(match_text),
            }
        )

    for m in DATE_PATTERN.finditer(normalized):
        add("date", m.group(0))

    for m in ADDRESS_PATTERN.finditer(normalized):
        candidate = m.group(0)
        # Guard against ordinance/resolution tails like "... 2026-14 for 10841 Douglas Avenue"
        # by re-anchoring to the last street-number phrase inside the matched span.
        if re.search(r"\bfor\s+\d", candidate, re.IGNORECASE):
            parts = re.split(r"\bfor\b", candidate, flags=re.IGNORECASE)
            if parts:
                maybe = normalize_text(parts[-1])
                if re.match(r"^\d{1,6}\b", maybe):
                    candidate = maybe
        add("address", candidate)

    for m in ORDINANCE_PATTERN.finditer(normalized):
        add("ordinance_number", m.group(1))

    for m in RESOLUTION_PATTERN.finditer(normalized):
        add("resolution_number", m.group(1))

    for m in ORG_PATTERN.finditer(normalized):
        add("organization", m.group(1))

    for m in PERSON_TITLED_PATTERN.finditer(normalized):
        add("person", m.group(1))

    return found


def _upsert_entity(db: Session, entity_type: str, display_value: str, normalized_value: str) -> Entity:
    row = (
        db.query(Entity)
        .filter(Entity.entity_type == entity_type, Entity.normalized_value == normalized_value)
        .one_or_none()
    )
    if not row:
        row = Entity(entity_type=entity_type, display_value=display_value, normalized_value=normalized_value)
        db.add(row)
        db.flush()
    elif not row.display_value:
        row.display_value = display_value
    if entity_type == "person":
        _upsert_entity_alias(
            db,
            entity_id=row.id,
            alias_text=row.display_value or display_value,
            source="person_seed",
            confidence=1.0,
        )
    return row


def _upsert_entity_alias(
    db: Session,
    *,
    entity_id: int,
    alias_text: str,
    source: str,
    confidence: float,
) -> EntityAlias | None:
    alias_text = normalize_text(alias_text)
    if not alias_text:
        return None
    normalized_alias = alias_text.lower()
    row = (
        db.query(EntityAlias)
        .filter(EntityAlias.entity_id == entity_id, EntityAlias.normalized_alias == normalized_alias)
        .one_or_none()
    )
    if not row:
        row = EntityAlias(
            entity_id=entity_id,
            alias_text=alias_text,
            normalized_alias=normalized_alias,
            source=source,
            confidence=confidence,
        )
        db.add(row)
    return row


def _add_person_alias_mentions(
    db: Session,
    *,
    meeting_id: int,
    source_type: str,
    source_id: int,
    context_text: str,
    agenda_item_id: int | None,
    document_id: int | None,
) -> list[EntityMention]:
    text = normalize_text(context_text)
    if not text:
        return []

    aliases = (
        db.query(EntityAlias, Entity)
        .join(Entity, Entity.id == EntityAlias.entity_id)
        .filter(Entity.entity_type == "person")
        .all()
    )
    mentions: list[EntityMention] = []
    existing_keys = {
        (row.entity_id, normalize_text(row.mention_text).lower())
        for row in (
            db.query(EntityMention)
            .filter(EntityMention.source_type == source_type, EntityMention.source_id == source_id)
            .all()
        )
    }
    for alias, entity in aliases:
        alias_text = normalize_text(alias.alias_text)
        if not alias_text:
            continue
        if not re.search(rf"\b{re.escape(alias_text)}\b", text, re.IGNORECASE):
            continue
        key = (entity.id, alias_text.lower())
        if key in existing_keys:
            continue
        mention = EntityMention(
            entity_id=entity.id,
            meeting_id=meeting_id,
            agenda_item_id=agenda_item_id,
            document_id=document_id,
            source_type=source_type,
            source_id=source_id,
            mention_text=alias_text,
            context_text=text[:2000],
            confidence=0.7,
        )
        db.add(mention)
        mentions.append(mention)
        existing_keys.add(key)
    return mentions


def replace_entity_mentions_for_source(
    db: Session,
    *,
    meeting_id: int,
    source_type: str,
    source_id: int,
    context_text: str,
    entities: list[dict[str, str]],
    agenda_item_id: int | None = None,
    document_id: int | None = None,
) -> list[EntityMention]:
    (
        db.query(EntityMention)
        .filter(EntityMention.source_type == source_type, EntityMention.source_id == source_id)
        .delete(synchronize_session=False)
    )

    mentions: list[EntityMention] = []
    context = normalize_text(context_text)[:2000]
    for ent in entities:
        entity = _upsert_entity(
            db,
            entity_type=ent["entity_type"],
            display_value=ent["display_value"],
            normalized_value=ent["normalized_value"],
        )
        mention = EntityMention(
            entity_id=entity.id,
            meeting_id=meeting_id,
            agenda_item_id=agenda_item_id,
            document_id=document_id,
            source_type=source_type,
            source_id=source_id,
            mention_text=ent["mention_text"],
            context_text=context,
            confidence=1.0,
        )
        db.add(mention)
        mentions.append(mention)

    # Second pass: snowball previously confirmed person entities using alias exact matches.
    mentions.extend(
        _add_person_alias_mentions(
            db,
            meeting_id=meeting_id,
            source_type=source_type,
            source_id=source_id,
            context_text=context,
            agenda_item_id=agenda_item_id,
            document_id=document_id,
        )
    )
    return mentions
