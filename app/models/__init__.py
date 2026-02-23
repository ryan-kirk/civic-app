
from sqlalchemy import String, Integer, Date, Time, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..db import Base

class Meeting(Base):
	__tablename__ = "meetings"

	meeting_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
	name: Mapped[str] = mapped_column(String, default="")
	date: Mapped[str] = mapped_column(String, default="")  # keep simple first; later use Date
	time: Mapped[str] = mapped_column(String, default="")
	location: Mapped[str] = mapped_column(String, default="")
	type_id: Mapped[int] = mapped_column(Integer, default=0)
	video_url: Mapped[str] = mapped_column(String, default="")

	agenda_items = relationship("AgendaItem", back_populates="meeting", cascade="all, delete-orphan")
	documents = relationship("Document", back_populates="meeting", cascade="all, delete-orphan")

class AgendaItem(Base):
	__tablename__ = "agenda_items"
	__table_args__ = (
		UniqueConstraint("meeting_id", "item_key", name="uq_meeting_item_key"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)

	section: Mapped[str] = mapped_column(String, default="")
	item_key: Mapped[str] = mapped_column(String, index=True)   # "6.17"
	title: Mapped[str] = mapped_column(Text, default="")

	topic: Mapped[str] = mapped_column(String, default="")      # later: zoning/other
	summary: Mapped[str] = mapped_column(Text, default="")      # later: LLM summary

	meeting = relationship("Meeting", back_populates="agenda_items")
	documents = relationship("Document", back_populates="agenda_item")

class Document(Base):
	__tablename__ = "documents"
	__table_args__ = (
		UniqueConstraint("meeting_id", "document_id", name="uq_meeting_document_id"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	agenda_item_id: Mapped[int | None] = mapped_column(ForeignKey("agenda_items.id"), nullable=True)

	document_id: Mapped[int] = mapped_column(Integer, index=True)   # 148134
	title: Mapped[str] = mapped_column(Text, default="")
	url: Mapped[str] = mapped_column(Text, default="")
	handle: Mapped[str] = mapped_column(String, default="")

	meeting = relationship("Meeting", back_populates="documents")
	agenda_item = relationship("AgendaItem", back_populates="documents")


class MeetingRawData(Base):
	__tablename__ = "meeting_raw_data"
	__table_args__ = (
		UniqueConstraint("meeting_id", name="uq_meeting_raw_data_meeting_id"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	meeting_data_json: Mapped[str] = mapped_column(Text, default="")
	meeting_documents_json: Mapped[str] = mapped_column(Text, default="")

	meeting = relationship("Meeting")


class MeetingRangeDiscoveryCache(Base):
	__tablename__ = "meeting_range_discovery_cache"
	__table_args__ = (
		UniqueConstraint(
			"from_date",
			"to_date",
			"crawl",
			"chunk_days",
			name="uq_range_discovery_cache_key",
		),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	from_date: Mapped[str] = mapped_column(String, index=True)
	to_date: Mapped[str] = mapped_column(String, index=True)
	crawl: Mapped[int] = mapped_column(Integer, default=1, index=True)  # sqlite bool-ish
	chunk_days: Mapped[int] = mapped_column(Integer, default=31)
	meeting_ids_json: Mapped[str] = mapped_column(Text, default="[]")
	discovered_count: Mapped[int] = mapped_column(Integer, default=0)
	last_fetched_at: Mapped[str] = mapped_column(String, default="")
	last_used_at: Mapped[str] = mapped_column(String, default="")


class MeetingMinutesMetadata(Base):
	__tablename__ = "meeting_minutes_metadata"
	__table_args__ = (
		UniqueConstraint("meeting_id", "document_id", name="uq_meeting_minutes_document"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	document_id: Mapped[int] = mapped_column(Integer, index=True)
	title: Mapped[str] = mapped_column(Text, default="")
	url: Mapped[str] = mapped_column(Text, default="")
	detected_date: Mapped[str] = mapped_column(String, default="")
	page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
	text_excerpt: Mapped[str] = mapped_column(Text, default="")
	status: Mapped[str] = mapped_column(String, default="unknown")

	meeting = relationship("Meeting")


class DocumentTextExtraction(Base):
	__tablename__ = "document_text_extractions"
	__table_args__ = (
		UniqueConstraint("meeting_id", "document_id", name="uq_document_text_extraction"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	document_id: Mapped[int] = mapped_column(Integer, index=True)
	title: Mapped[str] = mapped_column(Text, default="")
	url: Mapped[str] = mapped_column(Text, default="")
	content_type: Mapped[str] = mapped_column(String, default="")
	text_excerpt: Mapped[str] = mapped_column(Text, default="")
	text_length: Mapped[int] = mapped_column(Integer, default=0)
	status: Mapped[str] = mapped_column(String, default="unknown")

	meeting = relationship("Meeting")


class Entity(Base):
	__tablename__ = "entities"
	__table_args__ = (
		UniqueConstraint("entity_type", "normalized_value", name="uq_entity_type_normalized"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_type: Mapped[str] = mapped_column(String, index=True)
	display_value: Mapped[str] = mapped_column(Text, default="")
	normalized_value: Mapped[str] = mapped_column(String, index=True)


class EntityMention(Base):
	__tablename__ = "entity_mentions"
	__table_args__ = (
		UniqueConstraint(
			"entity_id",
			"source_type",
			"source_id",
			"mention_text",
			name="uq_entity_mention_source_text",
		),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	agenda_item_id: Mapped[int | None] = mapped_column(ForeignKey("agenda_items.id"), nullable=True, index=True)
	document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
	source_type: Mapped[str] = mapped_column(String, index=True)  # agenda_item_title, minutes_excerpt
	source_id: Mapped[int] = mapped_column(Integer, index=True)   # source row id in its table
	mention_text: Mapped[str] = mapped_column(Text, default="")
	context_text: Mapped[str] = mapped_column(Text, default="")
	confidence: Mapped[float] = mapped_column(default=1.0)

	entity = relationship("Entity")


class EntityAlias(Base):
	__tablename__ = "entity_aliases"
	__table_args__ = (
		UniqueConstraint("entity_id", "normalized_alias", name="uq_entity_alias"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	alias_text: Mapped[str] = mapped_column(Text, default="")
	normalized_alias: Mapped[str] = mapped_column(String, index=True)
	source: Mapped[str] = mapped_column(String, default="derived")
	confidence: Mapped[float] = mapped_column(default=1.0)

	entity = relationship("Entity")


class EntityBinding(Base):
	__tablename__ = "entity_bindings"
	__table_args__ = (
		UniqueConstraint("source_table", "source_id", name="uq_entity_binding_source"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	source_table: Mapped[str] = mapped_column(String, index=True)  # meetings, documents
	source_id: Mapped[int] = mapped_column(Integer, index=True)    # local PK for the source row

	entity = relationship("Entity")


class EntityConnection(Base):
	__tablename__ = "entity_connections"
	__table_args__ = (
		UniqueConstraint(
			"from_entity_id",
			"to_entity_id",
			"relation_type",
			"evidence_source_type",
			"evidence_source_id",
			name="uq_entity_connection_edge_evidence",
		),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	from_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	to_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	relation_type: Mapped[str] = mapped_column(String, index=True)  # contains_document, mentions, ...
	meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.meeting_id"), nullable=True, index=True)
	document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
	evidence_source_type: Mapped[str] = mapped_column(String, default="", index=True)  # source row provenance
	evidence_source_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
	strength: Mapped[float] = mapped_column(default=1.0)
	evidence_count: Mapped[int] = mapped_column(Integer, default=1)
	last_seen_at: Mapped[str] = mapped_column(String, default="")

	from_entity = relationship("Entity", foreign_keys=[from_entity_id])
	to_entity = relationship("Entity", foreign_keys=[to_entity_id])


class EntityPerson(Base):
	__tablename__ = "entity_people"
	__table_args__ = (
		UniqueConstraint("entity_id", name="uq_entity_people_entity"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	full_name: Mapped[str] = mapped_column(Text, default="")
	first_name: Mapped[str] = mapped_column(String, default="")
	last_name: Mapped[str] = mapped_column(String, default="")

	entity = relationship("Entity")


class EntityPlace(Base):
	__tablename__ = "entity_places"
	__table_args__ = (
		UniqueConstraint("entity_id", name="uq_entity_places_entity"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	address_text: Mapped[str] = mapped_column(Text, default="")
	city_hint: Mapped[str] = mapped_column(String, default="")
	state_hint: Mapped[str] = mapped_column(String, default="")
	zip_hint: Mapped[str] = mapped_column(String, default="")

	entity = relationship("Entity")


class EntityOrganization(Base):
	__tablename__ = "entity_organizations"
	__table_args__ = (
		UniqueConstraint("entity_id", name="uq_entity_organizations_entity"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	name_text: Mapped[str] = mapped_column(Text, default="")
	legal_suffix: Mapped[str] = mapped_column(String, default="")

	entity = relationship("Entity")


class EntityDateValue(Base):
	__tablename__ = "entity_dates"
	__table_args__ = (
		UniqueConstraint("entity_id", name="uq_entity_dates_entity"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
	date_iso: Mapped[str] = mapped_column(String, index=True, default="")
	label_text: Mapped[str] = mapped_column(Text, default="")

	entity = relationship("Entity")
