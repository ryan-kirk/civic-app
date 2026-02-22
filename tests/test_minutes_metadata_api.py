from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db
from app.models import Meeting, MeetingMinutesMetadata


def test_minutes_metadata_endpoint_returns_rows(tmp_path):
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
        db.add(
            MeetingMinutesMetadata(
                meeting_id=1408,
                document_id=149076,
                title="City Council Budget Work Session - February 7, 2026 - Minutes - Pdf",
                url="https://urbandale.civicweb.net/document/149076/City%20Council%20Budget%20Work%20Session%20-%20February%207,%20.pdf",
                detected_date="2026-02-07",
                page_count=11,
                text_excerpt="Budget work session minutes excerpt",
                status="ok",
            )
        )
        db.commit()

    try:
        client = TestClient(app)
        response = client.get("/meetings/1408/minutes-metadata")
        assert response.status_code == 200

        payload = response.json()
        assert len(payload) == 1
        row = payload[0]
        assert row["meeting_id"] == 1408
        assert row["document_id"] == 149076
        assert row["detected_date"] == "2026-02-07"
        assert row["page_count"] == 11
        assert row["status"] == "ok"
    finally:
        app.dependency_overrides.clear()
