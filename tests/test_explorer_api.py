from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.entities import extract_entities_from_text, replace_entity_mentions_for_source
from app.main import app, get_db
from app.models import AgendaItem, Meeting, MeetingRangeDiscoveryCache


def test_entity_suggest_and_explore_views(tmp_path):
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
        db.add(Meeting(meeting_id=5001, name="City Council - February 17, 2026", date="", time="", location="3600 86th Street", type_id=1, video_url=""))
        db.add(Meeting(meeting_id=5002, name="City Council - March 3, 2026", date="", time="", location="4020 121st Street", type_id=1, video_url=""))
        db.flush()

        texts = [
            (5001, "meeting_metadata", 5001, None, None, "Mayor Patricia Boddy Councilmember at 3600 86th Street on February 17, 2026"),
            (5002, "meeting_metadata", 5002, None, None, "MidAmerican Energy Company discussed 4020 121st Street on March 3, 2026"),
        ]
        for meeting_id, source_type, source_id, agenda_item_id, document_id, text in texts:
            replace_entity_mentions_for_source(
                db,
                meeting_id=meeting_id,
                source_type=source_type,
                source_id=source_id,
                agenda_item_id=agenda_item_id,
                document_id=document_id,
                context_text=text,
                entities=extract_entities_from_text(text),
            )

        item = AgendaItem(meeting_id=5002, item_key="9.5", section="", title="Purchase Agreement - 4020 121st Street")
        db.add(item)
        db.flush()
        replace_entity_mentions_for_source(
            db,
            meeting_id=5002,
            source_type="agenda_item_title",
            source_id=item.id,
            agenda_item_id=item.id,
            context_text=item.title,
            entities=extract_entities_from_text(item.title),
        )
        db.commit()

        db.add(
            MeetingRangeDiscoveryCache(
                from_date="2026-01-01",
                to_date="2026-02-28",
                crawl=1,
                chunk_days=31,
                meeting_ids_json="[5001,5002]",
                discovered_count=2,
                last_fetched_at="2026-02-22T12:00:00Z",
                last_used_at="2026-02-22T12:05:00Z",
            )
        )
        db.commit()

    try:
        client = TestClient(app)

        s = client.get("/entities/suggest", params={"q": "MidAmericn"})
        assert s.status_code == 200
        sp = s.json()
        assert sp
        assert any("MidAmerican" in row["display_value"] for row in sp)

        t = client.get("/explore/timeline")
        assert t.status_code == 200
        tp = t.json()
        assert tp
        assert any(row["date"] == "2026-03-03" for row in tp)

        m = client.get("/explore/locations")
        assert m.status_code == 200
        mp = m.json()
        assert mp
        loc = next(row for row in mp if "4020 121st Street" in row["address"])
        assert "4020 121st Street" in loc["map_query"]
        assert loc["state_hint"] == "Iowa"
        assert "zip_hint" in loc

        ent = client.get("/entities/search", params={"q": "MidAmerican"})
        assert ent.status_code == 200
        ep = ent.json()
        assert ep
        detail = client.get(f"/entities/{ep[0]['entity_id']}")
        assert detail.status_code == 200
        dp = detail.json()
        assert dp["mention_count"] >= 1
        assert any(m["context_text"] for m in dp["mentions"])

        pop = client.get("/explore/popular")
        assert pop.status_code == 200
        pp = pop.json()
        assert "entities" in pp and "topics" in pp
        assert pp["entities"]

        topics = client.get("/explore/topics")
        assert topics.status_code == 200
        tp2 = topics.json()
        assert tp2
        assert any(row["topic"] for row in tp2)

        stored = client.get("/stored/meetings", params={"limit": 10})
        assert stored.status_code == 200
        sp2 = stored.json()
        assert sp2
        row = next(r for r in sp2 if r["meeting_id"] == 5002)
        assert row["agenda_item_count"] >= 1
        assert row["entity_count"] >= 1

        stored_topic = client.get("/stored/meetings", params={"topic": "infrastructure_transport", "limit": 10})
        assert stored_topic.status_code == 200
        stp = stored_topic.json()
        assert any(r["matched_topic_count"] >= 1 for r in stp)

        stored_meeting_phrase = client.get("/stored/meetings", params={"q": "Meeting 5002", "limit": 10})
        assert stored_meeting_phrase.status_code == 200
        smp = stored_meeting_phrase.json()
        assert any(r["meeting_id"] == 5002 for r in smp)

        cov = client.get("/explore/coverage")
        assert cov.status_code == 200
        cv = cov.json()
        assert cv["meeting_count"] >= 2
        assert cv["entity_count"] >= 1
        assert cv["recent_discovery_ranges"]

        cache_status = client.get(
            "/ingest/cache-status",
            params={
                "from_date": "2026-01-01",
                "to_date": "2026-02-28",
                "crawl": "true",
                "chunk_days": 31,
                "cache_ttl_minutes": 999999,
            },
        )
        assert cache_status.status_code == 200
        cs = cache_status.json()
        assert cs["has_cache"] is True
        assert cs["cache_fresh"] is True
        assert cs["discovered_count"] == 2
    finally:
        app.dependency_overrides.clear()
