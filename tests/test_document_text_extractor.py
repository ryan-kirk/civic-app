from pathlib import Path

from app.document_text import extract_document_text


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "text/html"):
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


def test_extract_document_text_from_saved_lorey_html(monkeypatch):
    sample_path = Path("samples") / "Ordinance 2025-21 Lorey Property - Rezoning A-2 to R-1S.html"
    html_bytes = sample_path.read_bytes()

    monkeypatch.setattr("app.document_text.requests.get", lambda *args, **kwargs: _FakeResponse(html_bytes))

    result = extract_document_text(
        title="Ordinance 2025-21 Lorey Property - Rezoning A-2 to R-1S",
        url="https://urbandale.civicweb.net/document/146299/Ordinance%202025-21%20Lorey%20Property%20-%20Rezoning%20A-2%20to.doc",
    )

    assert result["status"] == "ok"
    assert result["content_type"] == "text/html"
    assert "16103 douglas parkway" in str(result["text_excerpt"]).lower()
    assert "Case No. 010-2025-01.03" in str(result["text_excerpt"])
