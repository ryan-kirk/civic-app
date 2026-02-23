import io

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from .models import DocumentTextExtraction
from .utils.text import normalize_text


def _extract_pdf_text(pdf_bytes: bytes) -> tuple[str, str]:
    try:
        from pypdf import PdfReader  # optional dependency
    except Exception:
        return "", "pdf_parser_unavailable"

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts: list[str] = []
        for page in reader.pages[:8]:
            txt = normalize_text(page.extract_text() or "")
            if txt:
                parts.append(txt)
        text = normalize_text(" ".join(parts))
        return text, "ok"
    except Exception:
        return "", "pdf_parse_failed"


def _extract_html_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_text(soup.get_text(" ", strip=True))


def extract_document_text(title: str, url: str) -> dict[str, str | int]:
    normalized_title = normalize_text(title)
    normalized_url = normalize_text(url)
    if not normalized_url:
        return {
            "content_type": "",
            "text_excerpt": "",
            "text_length": 0,
            "status": "missing_url",
        }

    try:
        response = requests.get(normalized_url, timeout=20)
        response.raise_for_status()
    except Exception:
        return {
            "content_type": "",
            "text_excerpt": "",
            "text_length": 0,
            "status": "download_failed",
        }

    content_type = normalize_text(response.headers.get("content-type", "").split(";")[0].strip().lower())
    body = response.content or b""
    text = ""
    status = "unsupported_content"

    if "pdf" in content_type or normalized_url.lower().endswith(".pdf"):
        text, status = _extract_pdf_text(body)
    else:
        decoded = ""
        for enc in ("utf-8", "latin-1"):
            try:
                decoded = body.decode(enc, errors="ignore")
                break
            except Exception:
                continue
        decoded_norm = normalize_text(decoded)
        looks_html = ("<html" in decoded.lower()) or ("text/html" in content_type) or ("aspose.words" in decoded.lower())
        if looks_html:
            text = _extract_html_text(decoded)
            status = "ok" if text else "html_parse_empty"
            if not content_type:
                content_type = "text/html"
        elif decoded_norm:
            text = decoded_norm
            status = "ok"
            if not content_type:
                content_type = "text/plain"

    if normalized_title and normalized_title not in text:
        # Keep title in context for entity extraction when body text is sparse.
        text = normalize_text(f"{normalized_title} {text}".strip())

    text = normalize_text(text)
    return {
        "content_type": content_type,
        "text_excerpt": text[:5000],
        "text_length": len(text),
        "status": status,
    }


def upsert_document_text_extraction_from_document(
    db: Session,
    *,
    meeting_id: int,
    document_id: int,
    title: str,
    url: str,
) -> DocumentTextExtraction:
    row = (
        db.query(DocumentTextExtraction)
        .filter(
            DocumentTextExtraction.meeting_id == meeting_id,
            DocumentTextExtraction.document_id == document_id,
        )
        .one_or_none()
    )
    if not row:
        row = DocumentTextExtraction(meeting_id=meeting_id, document_id=document_id)
        db.add(row)

    extracted = extract_document_text(title=title, url=url)
    row.title = normalize_text(title)
    row.url = normalize_text(url)
    row.content_type = str(extracted.get("content_type") or "")
    row.text_excerpt = str(extracted.get("text_excerpt") or "")
    row.text_length = int(extracted.get("text_length") or 0)
    row.status = str(extracted.get("status") or "unknown")
    return row
