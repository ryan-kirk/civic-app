import re

from app.utils.text import normalize_text

ZONE_TOKEN = r"(?:[A-Za-z]{1,4}\s*-\s*[A-Za-z0-9]{1,4}|[A-Za-z]{2,5})"

ORDINANCE_PATTERNS = [
    re.compile(r"\bordinance\s*(?:no\.?|number)?\s*([A-Z]?\d{1,4}[-/]\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\bord(?:inance)?\s*(?:no\.?)?\s*([A-Z]?\d{1,4}[-/]\d{1,4})\b", re.IGNORECASE),
]
READING_PATTERN = re.compile(r"\b(first|second|third|final)\s+reading\b", re.IGNORECASE)
THIRD_FINAL_PATTERN = re.compile(r"\bthird\s+and\s+final\s+reading\b", re.IGNORECASE)
ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Terrace|Ter|Place|Pl|Circle|Cir|Parkway|Pkwy)\b",
    re.IGNORECASE,
)
FROM_TO_PATTERN = re.compile(rf"\bfrom\s+({ZONE_TOKEN})\s+to\s+({ZONE_TOKEN})\b", re.IGNORECASE)
REZONE_TO_PATTERN = re.compile(rf"\brezone(?:d|s|ing)?\b.*?\b({ZONE_TOKEN})\s+to\s+({ZONE_TOKEN})\b", re.IGNORECASE)


def _clean_zone(zone: str | None) -> str | None:
    if not zone:
        return None
    zone = normalize_text(zone)
    zone = re.sub(r"\s*-\s*", "-", zone).upper()
    return zone or None


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return normalize_text(match.group(1))
    return None


def extract_zoning_signals(title: str, body: str | None = None) -> dict[str, str | None]:
    text = normalize_text(" ".join([title or "", body or ""]))

    from_zone = None
    to_zone = None

    from_to_match = FROM_TO_PATTERN.search(text)
    if from_to_match:
        from_zone = _clean_zone(from_to_match.group(1))
        to_zone = _clean_zone(from_to_match.group(2))
    else:
        rezone_to_match = REZONE_TO_PATTERN.search(text)
        if rezone_to_match:
            from_zone = _clean_zone(rezone_to_match.group(1))
            to_zone = _clean_zone(rezone_to_match.group(2))

    ordinance_number = _first_match(ORDINANCE_PATTERNS, text)
    reading_stage = None
    if THIRD_FINAL_PATTERN.search(text):
        reading_stage = "third"
    else:
        reading_match = READING_PATTERN.search(text)
        if reading_match:
            stage = reading_match.group(1).lower()
            reading_stage = "third" if stage == "final" else stage
    address_match = ADDRESS_PATTERN.search(text)

    return {
        "ordinance_number": ordinance_number,
        "from_zone": from_zone,
        "to_zone": to_zone,
        "reading_stage": reading_stage,
        "address": normalize_text(address_match.group(0)) if address_match else None,
    }
