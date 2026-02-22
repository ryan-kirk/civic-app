from app.minutes import extract_minutes_metadata, is_minutes_document


def test_minutes_url_sample_detects_date_when_download_fails(monkeypatch):
    title = "City Council Budget Work Session - February 7, 2026 - Minutes - Pdf"
    url = "https://urbandale.civicweb.net/document/149076/City%20Council%20Budget%20Work%20Session%20-%20February%207,%20.pdf"

    assert is_minutes_document(title)

    def fail_get(*args, **kwargs):
        raise RuntimeError("network disabled in test")

    monkeypatch.setattr("app.minutes.requests.get", fail_get)

    metadata = extract_minutes_metadata(title=title, url=url)
    assert metadata["detected_date"] == "2026-02-07"
    assert metadata["status"] == "download_failed"


def test_non_minutes_document_is_skipped():
    metadata = extract_minutes_metadata(
        title="Approve Resolution 052-2026",
        url="https://urbandale.civicweb.net/document/148875/sample.pdf",
    )
    assert metadata["status"] == "not_minutes"
    assert metadata["detected_date"] == ""
