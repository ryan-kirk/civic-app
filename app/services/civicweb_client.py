from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import httpx


@dataclass(frozen=True)
class CivicWebClient:
    base_url: str

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def list_meetings(self, date_from: str, date_to: str = "9999-12-31") -> List[Dict[str, Any]]:
        """
        CivicWeb API you found:
        /Services/MeetingsService.svc/meetings?from=YYYY-MM-DD&to=YYYY-MM-DD&_=...
        The `_` param is cache-busting; not required for correctness.
        """
        url = self._url("/Services/MeetingsService.svc/meetings")
        params = {"from": date_from, "to": date_to}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            # CivicWeb tends to return a JSON array here
            return data if isinstance(data, list) else [data]

    async def get_meeting_data(self, meeting_id: int) -> Dict[str, Any]:
        """
        CivicWeb API you found:
        /Services/MeetingsService.svc/meetings/{id}/meetingData?_=...
        """
        url = self._url(f"/Services/MeetingsService.svc/meetings/{meeting_id}/meetingData")

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()