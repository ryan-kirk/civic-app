import json
from pathlib import Path

from app.extractors.zoning import extract_zoning_signals
from app.classifiers.topics import classify_topics


SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"


def _load_json(name: str):
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


def test_extract_zoning_signals_from_samples():
    cases = _load_json("zoning_extraction_samples.json")
    for case in cases:
        assert extract_zoning_signals(case["text"]) == case["expected"]


def test_extract_zoning_signals_from_real_agenda_1408():
    items = _load_json("agenda_1408.json")

    zoning_items = []
    for item in items:
        title = item.get("title", "")
        docs_text = " ".join(d.get("title", "") for d in item.get("documents", []))
        topics = classify_topics(title, docs_text)
        if "zoning" in topics:
            zoning_items.append((item["item_key"], extract_zoning_signals(title, docs_text)))

    assert {item_key for item_key, _ in zoning_items} == {"6.16", "6.17"}

    signals_by_key = {item_key: signals for item_key, signals in zoning_items}
    assert signals_by_key["6.16"]["ordinance_number"] == "2026-13"
    assert signals_by_key["6.16"]["reading_stage"] == "third"
    assert signals_by_key["6.17"]["ordinance_number"] == "2026-14"
    assert signals_by_key["6.17"]["from_zone"] == "C-H"
    assert signals_by_key["6.17"]["to_zone"] == "PUD"
    assert signals_by_key["6.17"]["address"] == "10841 Douglas Avenue"
