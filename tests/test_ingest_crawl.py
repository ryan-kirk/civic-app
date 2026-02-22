from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingest import _collect_meeting_ids, ingest_range
from app.models import AgendaItem, Meeting, MeetingRawData


def _fake_meeting_data(mid: int) -> dict:
    return {
        "Name": f"Meeting {mid}",
        "Location": "City Hall",
        "Time": "6:00 PM",
        "TypeId": 1,
        "MeetingExternalLinkUrl": "",
    }


def _fake_meeting_documents(mid: int) -> list[dict]:
    html = f"<table><tr><td>6.1</td><td>Agenda Item {mid}</td></tr></table>"
    return [{"DocumentType": 1, "Html": html}]


def test_collect_meeting_ids_crawls_windows_and_dedupes(monkeypatch):
    calls = []

    def fake_list_meetings(date_from: str, date_to: str):
        calls.append((date_from, date_to))
        if date_from == "2026-01-01":
            return [{"Id": 1408}, {"Id": 1409}]
        if date_from == "2026-02-01":
            return [{"Id": 1409}, {"Id": 1410}]
        return []

    monkeypatch.setattr("app.ingest.cw.list_meetings", fake_list_meetings)

    ids = _collect_meeting_ids("2026-01-01", "2026-02-28", chunk_days=31)

    assert ids == [1408, 1409, 1410]
    assert calls == [
        ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
    ]


def test_ingest_range_crawl_stores_raw_data(monkeypatch, tmp_path):
    def fake_list_meetings(date_from: str, date_to: str):
        if date_from == "2026-01-01":
            return [{"Id": 1408}, {"Id": 1409}]
        if date_from == "2026-02-01":
            return [{"Id": 1409}, {"Id": 1410}]
        return []

    monkeypatch.setattr("app.ingest.cw.list_meetings", fake_list_meetings)
    monkeypatch.setattr("app.ingest.cw.get_meeting_data", _fake_meeting_data)
    monkeypatch.setattr("app.ingest.cw.get_meeting_documents", _fake_meeting_documents)

    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        result = ingest_range(
            db,
            from_date="2026-01-01",
            to_date="2026-02-28",
            limit=10,
            crawl=True,
            chunk_days=31,
            store_raw=True,
        )

        assert result["discovered"] == 3
        assert result["ingested"] == 3

        assert db.query(Meeting).count() == 3
        assert db.query(AgendaItem).count() == 3
        assert db.query(MeetingRawData).count() == 3

        raw = db.query(MeetingRawData).filter(MeetingRawData.meeting_id == 1408).one()
        assert "Meeting 1408" in raw.meeting_data_json
        assert "Agenda Item 1408" in raw.meeting_documents_json
