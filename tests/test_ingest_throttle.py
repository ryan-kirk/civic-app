from fastapi.testclient import TestClient

from app.main import app


def test_ingest_range_job_rejects_too_large_date_span(monkeypatch):
    client = TestClient(app)
    resp = client.post(
        "/ingest/range/job",
        params={
            "from_date": "2025-01-01",
            "to_date": "2025-12-31",
        },
    )
    assert resp.status_code == 400
    assert "date_range_too_large" in resp.json()["detail"]


def test_ingest_range_job_throttle_active_job(monkeypatch):
    monkeypatch.setattr("app.main.count_active_jobs", lambda: 1)
    monkeypatch.setattr("app.main.most_recent_job_created_at", lambda: None)
    client = TestClient(app)
    resp = client.post(
        "/ingest/range/job",
        params={
            "from_date": "2026-02-01",
            "to_date": "2026-02-22",
        },
    )
    assert resp.status_code == 429
    assert "ingest_job_limit_reached" in resp.json()["detail"]


def test_ingest_range_job_throttle_cooldown(monkeypatch):
    monkeypatch.setattr("app.main.count_active_jobs", lambda: 0)
    monkeypatch.setattr("app.main.most_recent_job_created_at", lambda: __import__("time").time())
    client = TestClient(app)
    resp = client.post(
        "/ingest/range/job",
        params={
            "from_date": "2026-02-01",
            "to_date": "2026-02-22",
        },
    )
    assert resp.status_code == 429
    assert "ingest_job_cooldown_active" in resp.json()["detail"]
