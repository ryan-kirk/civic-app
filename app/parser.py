import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

BASE = "https://urbandale.civicweb.net"

ITEM_KEY_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s*$")  # 6.17 or 6.17.
SECTION_LIKE_RE = re.compile(r"^[A-Z0-9' &\-]{4,}$")     # CONSENT AGENDA, CITIZENS' FORUM

def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return BASE + href

def parse_agenda_html(html: str):
    """
    Returns:
      agenda_items: list[dict] each:
        { item_key, section, title, attachments: [{document_id, title, url, handle}] }
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    current_section = ""
    items = []
    last_item = None

    for tbl in tables:
        # Flatten text in this table to help detect sections
        tbl_text = _clean_text(tbl.get_text(" ", strip=True))

        # Section detection (bold uppercase-ish label commonly appears)
        # We'll scan for a strong candidate by finding bold spans too.
        bold = tbl.find(["b", "strong"])
        if bold:
            candidate = _clean_text(bold.get_text(" ", strip=True))
            if candidate and SECTION_LIKE_RE.match(candidate) and len(candidate) <= 40:
                # Avoid treating "AGENDA" as section
                if candidate not in {"AGENDA"}:
                    current_section = candidate

        # Row-level parsing for item keys + titles
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            # Try to find an item key in any cell
            item_key = None
            key_td_idx = None
            for i, td in enumerate(tds):
                maybe = _clean_text(td.get_text(" ", strip=True))
                m = ITEM_KEY_RE.match(maybe)
                if m:
                    item_key = m.group(1)
                    key_td_idx = i
                    break

            if not item_key:
                continue

            # Title is usually in a later cell (often the last meaningful one)
            # We'll pick the longest non-key cell text as title.
            title_candidates = []
            for i, td in enumerate(tds):
                if i == key_td_idx:
                    continue
                text = _clean_text(td.get_text(" ", strip=True))
                if text:
                    title_candidates.append((len(text), i, text))
            if not title_candidates:
                continue
            title_candidates.sort(reverse=True)
            _, title_idx, title = title_candidates[0]

            # Attachments are <a href="/document/..."> links inside the title cell (or whole row)
            attachments = []
            for a in tds[title_idx].find_all("a", href=True):
                href = a["href"]
                if "/document/" not in href:
                    continue
                url = _abs_url(href)
                # Extract document_id from path /document/{id}/...
                doc_id = None
                parts = urlparse(url).path.split("/")
                try:
                    doc_pos = parts.index("document")
                    doc_id = int(parts[doc_pos + 1])
                except Exception:
                    continue

                qs = parse_qs(urlparse(url).query)
                handle = (qs.get("handle", [""])[0]) if qs else ""
                a_title = _clean_text(a.get_text(" ", strip=True)) or title

                attachments.append({
                    "document_id": doc_id,
                    "title": a_title,
                    "url": url,
                    "handle": handle,
                })

            last_item = {
                "item_key": item_key,
                "section": current_section,
                "title": title,
                "attachments": attachments,
            }
            items.append(last_item)

    return items