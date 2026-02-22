import io
import re
from datetime import datetime

import requests
from sqlalchemy.orm import Session

from .models import MeetingMinutesMetadata
from .utils.text import normalize_text

MINUTES_PATTERN = re.compile(r"\b(meeting\s+minutes?|minutes?)\b", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


def is_minutes_document(title: str) -> bool:
    return bool(MINUTES_PATTERN.search(normalize_text(title)))


def _extract_date_from_text(text: str) -> str:
    m = DATE_PATTERN.search(normalize_text(text))
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(0), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _extract_pdf_page_count_and_excerpt(pdf_bytes: bytes) -> tuple[int | None, str, str]:
    try:
        from pypdf import PdfReader  # optional dependency
    except Exception:
        return None, "", "pdf_parser_unavailable"

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        excerpt_parts = []
        for page in reader.pages[:2]:
            txt = normalize_text(page.extract_text() or "")
            if txt:
                excerpt_parts.append(txt)
        excerpt = normalize_text(" ".join(excerpt_parts))[:1200]
        return page_count, excerpt, "ok"
    except Exception:
        return None, "", "pdf_parse_failed"


def extract_minutes_metadata(title: str, url: str) -> dict[str, str | int | None]:
    normalized_title = normalize_text(title)
    normalized_url = normalize_text(url)

    if not is_minutes_document(normalized_title):
        return {
            "detected_date": "",
            "page_count": None,
            "text_excerpt": "",
            "status": "not_minutes",
        }

    if ".pdf" not in normalized_url.lower():
        return {
            "detected_date": _extract_date_from_text(normalized_title),
            "page_count": None,
            "text_excerpt": "",
            "status": "minutes_non_pdf",
        }

    try:
        response = requests.get(normalized_url, timeout=20)
        response.raise_for_status()
    except Exception:
        return {
            "detected_date": _extract_date_from_text(normalized_title),
            "page_count": None,
            "text_excerpt": "",
            "status": "download_failed",
        }

    page_count, excerpt, status = _extract_pdf_page_count_and_excerpt(response.content)
    detected_date = _extract_date_from_text(normalized_title)
    if not detected_date and excerpt:
        detected_date = _extract_date_from_text(excerpt)

    return {
        "detected_date": detected_date,
        "page_count": page_count,
        "text_excerpt": excerpt,
        "status": status,
    }


def upsert_minutes_metadata_from_document(
    db: Session,
    meeting_id: int,
    document_id: int,
    title: str,
    url: str,
) -> MeetingMinutesMetadata | None:
    if not is_minutes_document(title):
        return None

    meta = db.query(MeetingMinutesMetadata).filter(
        MeetingMinutesMetadata.meeting_id == meeting_id,
        MeetingMinutesMetadata.document_id == document_id,
    ).one_or_none()

    if not meta:
        meta = MeetingMinutesMetadata(meeting_id=meeting_id, document_id=document_id)
        db.add(meta)

    extracted = extract_minutes_metadata(title=title, url=url)

    meta.title = normalize_text(title)
    meta.url = normalize_text(url)
    meta.detected_date = str(extracted.get("detected_date") or "")
    meta.page_count = extracted.get("page_count") if isinstance(extracted.get("page_count"), int) else None
    meta.text_excerpt = str(extracted.get("text_excerpt") or "")
    meta.status = str(extracted.get("status") or "unknown")

    return meta
