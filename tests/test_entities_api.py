from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.entities import extract_entities_from_text, replace_entity_mentions_for_source
from app.ingest import ingest_meeting
from app.main import app, get_db
from app.models import AgendaItem, EntityMention, Meeting, MeetingMinutesMetadata


def test_meeting_entities_and_search_endpoint(tmp_path):
    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestingSessionLocal() as db:
        db.add(Meeting(meeting_id=1408, name="City Council", date="", time="", location="", type_id=1, video_url=""))
        item = AgendaItem(meeting_id=1408, item_key="6.17", section="", title="Ordinance 2026-14 for 10841 Douglas Avenue")
        db.add(item)
        db.flush()
        replace_entity_mentions_for_source(
            db,
            meeting_id=1408,
            agenda_item_id=item.id,
            source_type="agenda_item_title",
            source_id=item.id,
            context_text=item.title,
            entities=extract_entities_from_text(item.title),
        )

        minutes = MeetingMinutesMetadata(
            meeting_id=1408,
            document_id=149076,
            title="City Council Budget Work Session - February 7, 2026 - Minutes - Pdf",
            url="https://example.test/minutes.pdf",
            detected_date="2026-02-07",
            page_count=5,
            text_excerpt="The Enclave Apartments, LLC appeared before Council on February 7, 2026.",
            status="ok",
        )
        db.add(minutes)
        db.flush()
        replace_entity_mentions_for_source(
            db,
            meeting_id=1408,
            document_id=149076,
            source_type="minutes_excerpt",
            source_id=minutes.id,
            context_text=minutes.text_excerpt,
            entities=extract_entities_from_text(minutes.text_excerpt),
        )
        db.commit()

    try:
        client = TestClient(app)
        r = client.get("/meetings/1408/entities")
        assert r.status_code == 200
        payload = r.json()
        assert payload
        assert any(e["entity_type"] == "ordinance_number" and e["display_value"] == "2026-14" for e in payload)
        assert any(e["entity_type"] == "address" and "10841 Douglas Avenue" in e["display_value"] for e in payload)
        assert any(e["entity_type"] == "organization" and "Enclave Apartments" in e["display_value"] for e in payload)

        s = client.get("/entities/search", params={"q": "Enclave"})
        assert s.status_code == 200
        sp = s.json()
        assert sp
        enclave = next(e for e in sp if "Enclave Apartments" in e["display_value"])

        rel = client.get(f"/entities/{enclave['entity_id']}/related")
        assert rel.status_code == 200
        rp = rel.json()
        assert rp
        assert any(e["entity_type"] == "date" for e in rp)
    finally:
        app.dependency_overrides.clear()


def test_ingest_extracts_entities_from_meeting_metadata(monkeypatch, tmp_path):
    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def fake_get_meeting_data(meeting_id: int):
        return {
            "Name": "City Council - February 17, 2026",
            "Location": "3600 86th Street, Urbandale, Iowa",
            "Time": "6:00 PM",
            "TypeId": 1,
            "MeetingExternalLinkUrl": "",
        }

    def fake_get_meeting_documents(meeting_id: int):
        return [
            {
                "DocumentType": 1,
                "Html": "<table><tr><td>6.1</td><td>Approve Resolution 080-2026</td></tr></table>",
            }
        ]

    monkeypatch.setattr("app.ingest.cw.get_meeting_data", fake_get_meeting_data)
    monkeypatch.setattr("app.ingest.cw.get_meeting_documents", fake_get_meeting_documents)

    with TestingSessionLocal() as db:
        result = ingest_meeting(db, 1555, store_raw=False)
        assert result["status"] == "ok"

        client = TestClient(app)

        def override_get_db():
            db2 = TestingSessionLocal()
            try:
                yield db2
            finally:
                db2.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            response = client.get("/meetings/1555/entities")
            assert response.status_code == 200
            payload = response.json()
            assert any(e["entity_type"] == "date" and e["display_value"] == "February 17, 2026" for e in payload)
            assert any(e["entity_type"] == "address" and "3600 86th Street" in e["display_value"] for e in payload)
            # Meeting metadata entities should be traceable by source type.
            date_entity = next(e for e in payload if e["entity_type"] == "date" and e["display_value"] == "February 17, 2026")
            assert any(m["source_type"] == "meeting_metadata" for m in date_entity["mentions"])
        finally:
            app.dependency_overrides.clear()


def test_person_alias_snowball_matching_from_titled_seed(tmp_path):
    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        db.add(Meeting(meeting_id=2001, name="Council", date="", time="", location="", type_id=1, video_url=""))
        db.flush()

        # Seed high-confidence person entity from titled pattern.
        seed_text = "Mayor Jane Smith called the meeting to order."
        replace_entity_mentions_for_source(
            db,
            meeting_id=2001,
            source_type="meeting_metadata",
            source_id=2001,
            context_text=seed_text,
            entities=extract_entities_from_text(seed_text),
        )
        db.flush()

        # Later source has plain name only; alias match should attach the same person.
        item = AgendaItem(meeting_id=2001, item_key="9.1", section="", title="Discussion with Jane Smith regarding project timeline")
        db.add(item)
        db.flush()
        replace_entity_mentions_for_source(
            db,
            meeting_id=2001,
            agenda_item_id=item.id,
            source_type="agenda_item_title",
            source_id=item.id,
            context_text=item.title,
            entities=extract_entities_from_text(item.title),
        )
        db.flush()

        mentions = (
            db.query(EntityMention)
            .filter(EntityMention.source_type == "agenda_item_title", EntityMention.source_id == item.id)
            .all()
        )
        person_mentions = [m for m in mentions if m.entity.entity_type == "person"]
        assert person_mentions
        assert any(m.mention_text == "Jane Smith" for m in person_mentions)
        assert any(float(m.confidence) < 1.0 for m in person_mentions)  # alias snowball match
