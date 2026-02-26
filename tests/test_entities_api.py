from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.entities import extract_entities_from_text, replace_entity_mentions_for_source
from app.graph import backfill_graph_entities_and_connections
from app.ingest import ingest_meeting
from app.main import app, get_db
from app.models import (
    AgendaItem,
    Document,
    Entity,
    EntityBinding,
    EntityConnection,
    EntityDateValue,
    EntityMention,
    EntityOrganization,
    EntityPlace,
    Meeting,
    MeetingMinutesMetadata,
)


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


def test_person_alias_snowball_does_not_duplicate_direct_person_mention(tmp_path):
    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        db.add(Meeting(meeting_id=2002, name="Council", date="", time="", location="", type_id=1, video_url=""))
        db.flush()

        # Seed alias registry for Jane Smith.
        seed_text = "Mayor Jane Smith called the meeting to order."
        replace_entity_mentions_for_source(
            db,
            meeting_id=2002,
            source_type="meeting_metadata",
            source_id=2002,
            context_text=seed_text,
            entities=extract_entities_from_text(seed_text),
        )
        db.flush()

        # This source includes a titled person mention (direct extractor) that also matches alias snowball.
        minutes = MeetingMinutesMetadata(
            meeting_id=2002,
            document_id=555001,
            title="City Council Minutes - Pdf",
            url="https://example.test/minutes.pdf",
            detected_date="2026-02-03",
            page_count=2,
            text_excerpt="Mayor Jane Smith welcomed attendees and opened the meeting.",
            status="ok",
        )
        db.add(minutes)
        db.flush()

        replace_entity_mentions_for_source(
            db,
            meeting_id=2002,
            document_id=minutes.document_id,
            source_type="minutes_excerpt",
            source_id=minutes.id,
            context_text=minutes.text_excerpt,
            entities=extract_entities_from_text(minutes.text_excerpt),
        )
        db.commit()

        mentions = (
            db.query(EntityMention)
            .filter(EntityMention.source_type == "minutes_excerpt", EntityMention.source_id == minutes.id)
            .all()
        )
        person_mentions = [m for m in mentions if m.entity.entity_type == "person"]
        assert len(person_mentions) == 1
        assert person_mentions[0].mention_text == "Jane Smith"
        assert float(person_mentions[0].confidence) == 1.0


def test_graph_backfill_promotes_meeting_and_document_entities_and_connections(tmp_path):
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
        meeting = Meeting(
            meeting_id=1408,
            name="City Council - February 17, 2026",
            date="",
            time="05:30 PM",
            location="3600 86th Street",
            type_id=1,
            video_url="",
        )
        db.add(meeting)
        db.flush()
        doc = Document(
            meeting_id=1408,
            agenda_item_id=None,
            document_id=149076,
            title="City Council Budget Work Session - February 7, 2026 - Minutes - Pdf",
            url="https://example.test/minutes.pdf",
            handle="h1",
        )
        db.add(doc)
        db.flush()

        replace_entity_mentions_for_source(
            db,
            meeting_id=1408,
            source_type="meeting_metadata",
            source_id=1408,
            context_text="City Council - February 17, 2026 at 3600 86th Street",
            entities=extract_entities_from_text("City Council - February 17, 2026 at 3600 86th Street"),
        )
        replace_entity_mentions_for_source(
            db,
            meeting_id=1408,
            document_id=149076,
            source_type="document_content",
            source_id=doc.id,
            context_text="The Enclave Apartments, LLC discussed rezoning at 10841 Douglas Avenue on February 17, 2026.",
            entities=extract_entities_from_text(
                "The Enclave Apartments, LLC discussed rezoning at 10841 Douglas Avenue on February 17, 2026."
            ),
        )
        db.flush()

        assert db.query(EntityPlace).count() >= 1
        assert db.query(EntityOrganization).count() >= 1
        assert db.query(EntityDateValue).count() >= 1

        stats = backfill_graph_entities_and_connections(db, meeting_id=1408)
        assert stats["processed_meetings"] == 1

        meeting_entity = (
            db.query(Entity)
            .filter(Entity.entity_type == "meeting", Entity.normalized_value == "meeting:1408")
            .one_or_none()
        )
        assert meeting_entity is not None
        document_entity = (
            db.query(Entity)
            .filter(Entity.entity_type == "document", Entity.normalized_value == "document:1408:149076")
            .one_or_none()
        )
        assert document_entity is not None

        assert (
            db.query(EntityBinding)
            .filter(EntityBinding.entity_id == meeting_entity.id, EntityBinding.source_table == "meetings", EntityBinding.source_id == 1408)
            .one_or_none()
        ) is not None
        assert (
            db.query(EntityBinding)
            .filter(EntityBinding.entity_id == document_entity.id, EntityBinding.source_table == "documents", EntityBinding.source_id == doc.id)
            .one_or_none()
        ) is not None

        assert (
            db.query(EntityConnection)
            .filter(
                EntityConnection.from_entity_id == meeting_entity.id,
                EntityConnection.to_entity_id == document_entity.id,
                EntityConnection.relation_type == "contains_document",
            )
            .one_or_none()
        ) is not None

    try:
        client = TestClient(app)
        search = client.get("/entities/search", params={"q": "Meeting 1408"})
        assert search.status_code == 200
        rows = search.json()
        meeting_row = next((r for r in rows if r["entity_type"] == "meeting" and r["normalized_value"] == "meeting:1408"), None)
        assert meeting_row is not None
        assert any(b["source_table"] == "meetings" and b["source_id"] == 1408 for b in meeting_row.get("bindings", []))

        conn = client.get(f"/entities/{meeting_row['entity_id']}/connections")
        assert conn.status_code == 200
        cp = conn.json()
        assert cp
        doc_conn = next((r for r in cp if r["entity_type"] == "document" and r["relation_type"] == "contains_document"), None)
        assert doc_conn is not None
        assert any(b["source_table"] == "documents" for b in doc_conn.get("bindings", []))
        assert any(r["entity_type"] in {"address", "date", "organization"} for r in cp)

        conn_zoning = client.get(
            f"/entities/{meeting_row['entity_id']}/connections",
            params={"topic": "zoning"},
        )
        assert conn_zoning.status_code == 200
        cp_zoning = conn_zoning.json()
        assert cp_zoning
        assert any("10841 Douglas Avenue" in (r.get("display_value") or "") for r in cp_zoning)
        assert not any("3600 86th Street" in (r.get("display_value") or "") for r in cp_zoning)

        conn_dates = client.get(
            f"/entities/{meeting_row['entity_id']}/connections",
            params={"entity_type": "date"},
        )
        assert conn_dates.status_code == 200
        cp_dates = conn_dates.json()
        assert cp_dates
        assert all((r.get("entity_type") or "") == "date" for r in cp_dates)

        evid = client.get(
            f"/entities/{meeting_row['entity_id']}/connections/{doc_conn['entity_id']}/evidence",
            params={"relation_type": "contains_document", "direction": "outgoing"},
        )
        assert evid.status_code == 200
        ep = evid.json()
        assert ep
        assert any(row["evidence_source_type"] == "documents" for row in ep)
        assert any("City Council Budget Work Session" in (row["context_text"] or "") for row in ep)
    finally:
        app.dependency_overrides.clear()
