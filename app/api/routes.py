from __future__ import annotations

from fastapi import APIRouter, Query
from app.config import settings
from app.services.civicweb_client import CivicWebClient

router = APIRouter()
client = CivicWebClient(base_url=settings.civicweb_base_url)


@router.get("/health")
async def health():
    return {"status": "ok"}


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