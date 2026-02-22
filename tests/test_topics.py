import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.classifiers.topics import classify_topics
from app.db import Base
from app.main import app, get_db
from app.models import AgendaItem, Document, Meeting
from app.utils.text import normalize_text


SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"


def _load_json(name: str):
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


def test_classify_topics_from_samples():
    cases = _load_json("topic_classification_samples.json")
    for case in cases:
        topics = sorted(classify_topics(case["text"]))
        assert topics == case["expected_topics"]


def test_normalize_text_unescapes_and_normalizes_punctuation():
    src = "Title 15\u00a0Chapter 160 &amp; Zoning\u2014Updates\u2026"
    assert normalize_text(src) == "Title 15 Chapter 160 & Zoning-Updates..."


def test_agenda_topic_filter_returns_only_zoning(tmp_path):
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

    data = _load_json("agenda_items_sample.json")
    with TestingSessionLocal() as db:
        db.add(Meeting(meeting_id=9999, name="Sample Meeting", date="", time="", location="", type_id=1, video_url=""))
        db.flush()

        for item_data in data:
            item = AgendaItem(
                meeting_id=9999,
                item_key=item_data["item_key"],
                section=item_data["section"],
                title=item_data["title"],
            )
            db.add(item)
            db.flush()

            for doc_data in item_data["documents"]:
                db.add(
                    Document(
                        meeting_id=9999,
                        agenda_item_id=item.id,
                        document_id=doc_data["document_id"],
                        title=doc_data["title"],
                        url=doc_data["url"],
                        handle=doc_data["handle"],
                    )
                )

        db.commit()

    try:
        client = TestClient(app)

        all_items = client.get("/meetings/9999/agenda")
        assert all_items.status_code == 200
        assert len(all_items.json()) == 3

        filtered = client.get("/meetings/9999/agenda", params={"topic": "zoning"})
        assert filtered.status_code == 200
        payload = filtered.json()

        assert len(payload) == 2
        assert all("zoning" in i["topics"] for i in payload)
        assert all(i["zoning_signals"] is not None for i in payload)
        assert any(i["zoning_signals"]["from_zone"] == "C-H" and i["zoning_signals"]["to_zone"] == "PUD" for i in payload)
    finally:
        app.dependency_overrides.clear()
