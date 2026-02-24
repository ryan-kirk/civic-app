from pydantic import BaseModel, Field
from typing import List, Optional

class DocumentOut(BaseModel):
    document_id: int
    title: str
    url: str
    handle: str


class ZoningSignalsOut(BaseModel):
    ordinance_number: Optional[str] = None
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    reading_stage: Optional[str] = None
    address: Optional[str] = None


class AgendaItemOut(BaseModel):
    item_key: str
    section: str
    title: str
    topics: List[str] = Field(default_factory=list)
    zoning_signals: Optional[ZoningSignalsOut] = None
    documents: List[DocumentOut] = Field(default_factory=list)


class MeetingMinutesMetadataOut(BaseModel):
    meeting_id: int
    document_id: int
    title: str
    url: str
    detected_date: str
    page_count: Optional[int] = None
    text_excerpt: str
    status: str


class EntityMentionOut(BaseModel):
    meeting_id: int
    source_type: str
    source_id: int
    agenda_item_id: Optional[int] = None
    document_id: Optional[int] = None
    mention_text: str
    context_text: str
    confidence: float


class EntityBindingOut(BaseModel):
    source_table: str
    source_id: int


class EntitySummaryOut(BaseModel):
    entity_id: int
    entity_type: str
    display_value: str
    normalized_value: str
    mention_count: int
    kind_metadata: dict[str, str] = Field(default_factory=dict)
    bindings: List[EntityBindingOut] = Field(default_factory=list)
    mentions: List[EntityMentionOut] = Field(default_factory=list)


class RelatedEntityOut(BaseModel):
    entity_id: int
    entity_type: str
    display_value: str
    normalized_value: str
    cooccurrence_count: int
    shared_meeting_count: int


class EntityConnectionOut(BaseModel):
    entity_id: int
    entity_type: str
    display_value: str
    normalized_value: str
    relation_type: str
    direction: str
    edge_count: int
    evidence_count: int
    shared_meeting_count: int
    kind_metadata: dict[str, str] = Field(default_factory=dict)
    bindings: List[EntityBindingOut] = Field(default_factory=list)


class ConnectionEvidenceOut(BaseModel):
    relation_type: str
    direction: str
    meeting_id: Optional[int] = None
    document_id: Optional[int] = None
    evidence_source_type: str
    evidence_source_id: int
    strength: float
    mention_text: str = ""
    context_text: str = ""


class EntitySuggestOut(BaseModel):
    entity_id: int
    entity_type: str
    display_value: str
    score: float


class AgendaTopicSearchOut(BaseModel):
    meeting_id: int
    agenda_item_id: int
    item_key: str
    title: str
    section: str


class DocumentSearchOut(BaseModel):
    meeting_id: int
    document_id: int
    agenda_item_id: Optional[int] = None
    title: str
    url: str


class TimelineBucketOut(BaseModel):
    date: str
    label: str
    meeting_ids: List[int] = Field(default_factory=list)
    entity_count: int


class AddressExploreOut(BaseModel):
    entity_id: int
    address: str
    city_hint: str
    state_hint: str
    zip_hint: str
    map_query: str
    shared_meeting_count: int
    mention_count: int


class PopularTopicOut(BaseModel):
    topic: str
    count: int


class ExplorePopularOut(BaseModel):
    topics: List[PopularTopicOut] = Field(default_factory=list)
    entities: List[EntitySummaryOut] = Field(default_factory=list)


class MeetingOut(BaseModel):
    meeting_id: int
    name: str
    date: str
    time: str
    location: str
    type_id: int
    video_url: str


class StoredMeetingSummaryOut(BaseModel):
    meeting_id: int
    name: str
    date: str
    time: str
    location: str
    agenda_item_count: int
    document_count: int
    entity_count: int
    minutes_count: int
    matched_topic_count: int = 0


class ExploreTopicSummaryOut(BaseModel):
    topic: str
    agenda_item_count: int
    meeting_count: int
    recent_meeting_ids: List[int] = Field(default_factory=list)
