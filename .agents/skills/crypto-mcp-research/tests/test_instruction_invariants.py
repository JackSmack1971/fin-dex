import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = Path(__file__).parent / "fixtures/instruction-invariants-v1.json"
EXPECTED = {"REV-01", "REV-02", "REV-03", "REV-04", "REV-05", "REV-06", "EVID-01", "EVID-02", "EVID-03", "MCP-01", "AUTH-01", "AUTH-02", "LIFE-01", "EVAL-01", "CFG-01", "AGENT-01", "AGENT-02", "AGENT-03", "AGENT-04", "AGENT-05", "AGENT-06", "OVERLAY-01"}

def test_invariants_have_one_or_more_existing_owners():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    entries = data["invariants"]
    assert {item["id"] for item in entries} == EXPECTED
    assert len(entries) == len(EXPECTED)
    for item in entries:
        assert item["primary_owner"]
        for owner in [item["primary_owner"], *item["references"]]:
            assert (ROOT / owner).resolve().exists(), (item["id"], owner)

def test_positive_negative_and_near_neighbor_routing_cases_exist():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["routing_cases"]
    assert all(cases[key] for key in ("positive", "negative", "near_neighbor"))

def test_overlay_disposition_summary_is_frozen():
    audit = json.loads(FIXTURE.read_text(encoding="utf-8"))["overlay_audit"]
    assert audit["entries"] == 193
    assert audit["dispositions"] == {"ADOPT": 4, "DEFER": 5, "EXCLUDE": 184}
    assert audit["hash_mismatches"] == 0

