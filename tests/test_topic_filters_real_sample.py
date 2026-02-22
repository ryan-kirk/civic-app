import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db
from app.models import AgendaItem, Document, Meeting


SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"


def _load_json(name: str):
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


def test_topic_filters_work_for_new_labels_with_agenda_1408(tmp_path):
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

    items = _load_json("agenda_1408.json")
    with TestingSessionLocal() as db:
        db.add(Meeting(meeting_id=1408, name="City Council", date="", time="", location="", type_id=1, video_url=""))
        db.flush()

        for item_data in items:
            item = AgendaItem(
                meeting_id=1408,
                item_key=item_data["item_key"],
                section=item_data.get("section", ""),
                title=item_data["title"],
            )
            db.add(item)
            db.flush()

            for doc_data in item_data.get("documents", []):
                db.add(
                    Document(
                        meeting_id=1408,
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

        topics_to_check = [
            "zoning",
            "ordinances_general",
            "public_hearings",
            "contracts_procurement",
            "budget_finance",
            "urban_renewal_development",
        ]

        for topic in topics_to_check:
            response = client.get(f"/meetings/1408/agenda", params={"topic": topic})
            assert response.status_code == 200
            payload = response.json()
            assert payload, f"Expected results for topic={topic}"
            assert all(topic in item["topics"] for item in payload)

        zoning_payload = client.get("/meetings/1408/agenda", params={"topic": "zoning"}).json()
        assert {item["item_key"] for item in zoning_payload} == {"6.16", "6.17"}
    finally:
        app.dependency_overrides.clear()
