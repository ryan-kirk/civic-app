import html
import re

def normalize_text(s: str) -> str:
    """
    Normalize text for downstream matching/search:
    - HTML entity unescape (&quot; -> ")
    - Collapse whitespace
    - Normalize curly quotes/dashes (optional)
    """
    if not s:
        return ""
    s = html.unescape(s)                 # &quot; -> ", &amp; -> &
    s = s.replace("\u00a0", " ")         # nbsp
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2026", "...")
    s = re.sub(r"\s+", " ", s).strip()   # collapse whitespace
    return s
