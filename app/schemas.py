from pydantic import BaseModel
from typing import List, Optional

class DocumentOut(BaseModel):
    document_id: int
    title: str
    url: str
    handle: str

class AgendaItemOut(BaseModel):
    item_key: str
    section: str
    title: str
    documents: List[DocumentOut] = []

class MeetingOut(BaseModel):
    meeting_id: int
    name: str
    date: str
    time: str
    location: str
    type_id: int
    video_url: str