from app.main import app


def test_api_route_contract():
    routes = {
        (route.path, tuple(sorted(route.methods - {"HEAD", "OPTIONS"})))
        for route in app.routes
        if hasattr(route, "path") and hasattr(route, "methods")
    }

    expected = {
        ("/health", ("GET",)),
        ("/meetings", ("GET",)),
        ("/meetings/{meeting_id}", ("GET",)),
        ("/meetings/{meeting_id}/agenda", ("GET",)),
        ("/ingest/meeting/{meeting_id}", ("POST",)),
        ("/ingest/range", ("POST",)),
    }

    missing = expected - routes
    assert not missing, f"Missing expected API routes: {sorted(missing)}"
