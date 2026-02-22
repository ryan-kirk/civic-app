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

class MeetingOut(BaseModel):
    meeting_id: int
    name: str
    date: str
    time: str
    location: str
    type_id: int
    video_url: str
