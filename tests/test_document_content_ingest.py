from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingest import ingest_meeting
from app.models import DocumentTextExtraction, Entity, EntityMention


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "text/html"):
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


def test_ingest_extracts_document_content_entities_from_html_doc(monkeypatch, tmp_path):
    sample_path = Path("samples") / "Ordinance 2025-21 Lorey Property - Rezoning A-2 to R-1S.html"
    html_bytes = sample_path.read_bytes()

    def fake_get_meeting_data(meeting_id: int):
        return {
            "Name": "City Council - December 2, 2025",
            "Location": "Council Chambers - 3600 86th Street",
            "Time": "6:00 PM",
            "TypeId": 1,
            "MeetingExternalLinkUrl": "",
        }

    def fake_get_meeting_documents(meeting_id: int):
        return [{"DocumentType": 1, "Html": "<table></table>"}]

    def fake_parse_agenda_html(_html: str):
        return [
            {
                "item_key": "6.3",
                "section": "PUBLIC HEARINGS",
                "title": "Approve the Second Reading of Ordinance 2025-21 Lorey Property rezoning.",
                "attachments": [
                    {
                        "document_id": 146299,
                        "title": "Ordinance 2025-21 Lorey Property - Rezoning A-2 to R-1S",
                        "url": "https://urbandale.civicweb.net/document/146299/Ordinance%202025-21%20Lorey%20Property%20-%20Rezoning%20A-2%20to.doc",
                        "handle": "abc",
                    }
                ],
            }
        ]

    monkeypatch.setattr("app.ingest.cw.get_meeting_data", fake_get_meeting_data)
    monkeypatch.setattr("app.ingest.cw.get_meeting_documents", fake_get_meeting_documents)
    monkeypatch.setattr("app.ingest.parse_agenda_html", fake_parse_agenda_html)
    monkeypatch.setattr("app.document_text.requests.get", lambda *args, **kwargs: _FakeResponse(html_bytes))

    test_db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        result = ingest_meeting(db, 1372, store_raw=False)
        assert result["status"] == "ok"

        ext = (
            db.query(DocumentTextExtraction)
            .filter(DocumentTextExtraction.meeting_id == 1372, DocumentTextExtraction.document_id == 146299)
            .one()
        )
        assert ext.status == "ok"
        assert "16103 douglas parkway" in ext.text_excerpt.lower()

        addr_entity = (
            db.query(Entity)
            .filter(Entity.entity_type == "address", Entity.normalized_value.like("%16103 douglas parkway%"))
            .one_or_none()
        )
        assert addr_entity is not None

        mention = (
            db.query(EntityMention)
            .filter(
                EntityMention.entity_id == addr_entity.id,
                EntityMention.meeting_id == 1372,
                EntityMention.document_id == 146299,
                EntityMention.source_type == "document_content",
            )
            .one_or_none()
        )
        assert mention is not None
