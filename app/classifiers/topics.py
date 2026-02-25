import re
from app.utils.text import normalize_text

TOPIC_PATTERNS: dict[str, list[str]] = {
    "zoning": [
        r"\bzoning\b",
        r"\brezone\b",
        r"\brezoning\b",
        r"\bchapter\s*160\b",
        r"\btitle\s*(xv|15)\b",  # Title XV / Title 15
        r"\bpud\b",
        r"\bplanned unit development\b",
        r"\b(c-\s*h|c-h)\b",  # C-H
        r"\b(hwy|highway)\s+commercial\b",
        r"\btitle\s*(xv|15)\s*chapter\s*160\s*zoning\b",
        r"\brezone\b.*\bc-\s*h\b.*\bto\b.*\bpud\b",
    ],
    "ordinances_general": [
        r"\bordinance\b",
        r"\b(first|second|third|final)\s+reading\b",
    ],
    "public_hearings": [
        r"\bpublic hearing\b",
        r"\bestablish public hearing\b",
    ],
    "schools": [
        r"\bschools?\b",
        r"\bschool district\b",
        r"\beducation(?:al)?\b",
        r"\bstudents?\b",
    ],
    "public_safety": [
        r"\bpublic safety\b",
        r"\bpolice(?:\s+department)?\b",
        r"\bfire(?:\s+ems|\s+department)?\b",
        r"\bems\b",
        r"\bemergency medical\b",
        r"\btraffic safety\b",
        r"\blaw enforcement\b",
    ],
    "enforcement": [
        r"\bcode enforcement\b",
        r"\benforcement\b",
        r"\bmunicipal infractions?\b",
        r"\binfractions?\b",
        r"\bcitations?\b",
        r"\bviolations?\b",
    ],
    "contracts_procurement": [
        r"\bbids?\b",
        r"\baward of contract\b",
        r"\bapproving contract\b",
        r"\bprofessional services agreement\b",
        r"\bcontract\b",
    ],
    "budget_finance": [
        r"\bbudget\b",
        r"\bbill lists?\b",
        r"\bfinancial statements?\b",
        r"\bcash position\b",
        r"\bproperty tax\b",
        r"\bcapital loan notes?\b",
    ],
    "infrastructure_transport": [
        r"\bpaving\b",
        r"\bsidewalk\b",
        r"\bstreet\b",
        r"\bsewer\b",
        r"\bpatch program\b",
    ],
    "urban_renewal_development": [
        r"\burban renewal\b",
        r"\bdevelopment agreement\b",
        r"\bconveyance of property\b",
    ],
    "boards_commissions": [
        r"\bboard of adjustment\b",
        r"\bcivil service\b",
        r"\badvisory board\b",
        r"\bboards?\s+and\s+commissions?\b",
    ],
    "licenses_permits": [
        r"\bbusiness licenses?\b",
        r"\bbuilding permit\b",
        r"\bpermit report\b",
    ],
    "utilities_franchise": [
        r"\belectric franchise\b",
        r"\bgas franchise\b",
        r"\bmidamerican\b",
    ],
}

def classify_topics(title: str, body: str | None = None) -> set[str]:
    text = normalize_text(" ".join([title or "", body or ""])).lower()
    topics: set[str] = set()

    for topic, patterns in TOPIC_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text):
                topics.add(topic)
                break

    return topics
