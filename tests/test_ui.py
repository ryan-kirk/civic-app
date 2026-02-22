from fastapi.testclient import TestClient

from app.main import app


def test_ui_home_serves_html():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "CivicWatch Explorer" in response.text
