from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db
from app.models import AgendaItem, Document, Meeting


def test_content_search_returns_agenda_topics_and_documents(tmp_path):
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
        db.add(Meeting(meeting_id=3001, name="Council", date="", time="", location="", type_id=1, video_url=""))
        item = AgendaItem(meeting_id=3001, item_key="9.5", section="", title="Purchase Agreement - 4020 121st Street, Urbandale, Iowa")
        db.add(item)
        db.flush()
        db.add(Document(meeting_id=3001, agenda_item_id=item.id, document_id=9001, title="Council Letter 8392 - Pdf", url="https://example.test/8392.pdf", handle="h"))
        db.commit()

    try:
        client = TestClient(app)
        r = client.get("/search/content", params={"q": "8392"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["documents"]
        assert payload["documents"][0]["document_id"] == 9001

        r2 = client.get("/search/content", params={"q": "121st Street"})
        assert r2.status_code == 200
        payload2 = r2.json()
        assert payload2["agenda_topics"]
        assert payload2["agenda_topics"][0]["item_key"] == "9.5"
    finally:
        app.dependency_overrides.clear()
