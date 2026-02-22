from app.entities import extract_entities_from_text


def test_extract_entities_from_text_detects_core_types():
    text = (
        "Approve the Third and Final Reading of Ordinance 2026-14 for 10841 Douglas Avenue "
        "with The Enclave Apartments, LLC on February 18, 2026 and Resolution 080-2026"
    )
    rows = extract_entities_from_text(text)
    by_type = {(r["entity_type"], r["normalized_value"]) for r in rows}

    assert ("ordinance_number", "2026-14") in by_type
    assert ("resolution_number", "080-2026") in by_type
    assert ("address", "10841 douglas avenue") in by_type
    assert ("date", "2026-02-18") in by_type
    assert any(r["entity_type"] == "organization" and "enclave apartments" in r["normalized_value"] for r in rows)
