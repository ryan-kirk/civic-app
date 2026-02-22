import requests

BASE = "https://urbandale.civicweb.net"

def _get_json(url: str, timeout=30):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def list_meetings(from_date: str, to_date: str):
    url = f"{BASE}/Services/MeetingsService.svc/meetings?from={from_date}&to={to_date}"
    return _get_json(url)

def get_meeting_data(meeting_id: int):
    url = f"{BASE}/Services/MeetingsService.svc/meetings/{meeting_id}/meetingData"
    return _get_json(url)

def get_meeting_documents(meeting_id: int):
    url = f"{BASE}/Services/MeetingsService.svc/meetings/{meeting_id}/meetingDocuments?$format=json"
    return _get_json(url)