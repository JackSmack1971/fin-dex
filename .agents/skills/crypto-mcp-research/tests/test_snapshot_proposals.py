import importlib.util
import datetime as dt
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/snapshot_proposals.py"
SPEC = importlib.util.spec_from_file_location("snapshot_proposals", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
Client = module.SnapshotProposalClient
Error = module.ProviderError
SPACE = "yam.eth"


def proposal(pid="p1", created=1_700_000_000, space=SPACE):
    return {"id": pid, "title": "Upgrade proposal", "author": "0xabc",
            "created": created, "start": created + 100, "end": created + 200,
            "state": "closed", "space": {"id": space, "name": "Yam"}}


def payload(rows):
    return {"data": {"proposals": rows}}


class Response:
    status = 200

    def __init__(self, value, url=module.ENDPOINT):
        self.raw = json.dumps(value).encode()
        self.url = url

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.raw[:size]
    def geturl(self): return self.url


def client_with(value, calls=None):
    def opener(request, timeout):
        if calls is not None:
            calls.append((request, timeout))
        return Response(value)
    return Client(opener=opener, utc_clock=lambda: dt.datetime(2023, 11, 15, tzinfo=dt.timezone.utc))


def test_fixed_query_is_anonymous_bounded_and_normalizes_seconds():
    calls = []
    client = client_with(payload([proposal()]), calls)
    result = client.proposals(SPACE, limit=1)
    request, timeout = calls[0]
    body = json.loads(request.data)
    assert request.full_url == module.ENDPOINT
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") is None
    assert body["variables"] == {"space": SPACE, "first": 1}
    assert "proposals(" in body["query"]
    assert timeout == 10
    assert result["identity_status"] == "VERIFIED"
    assert result["http_status"] == 200
    assert result["proposals"][0]["created_at_utc"] == "2023-11-14T22:13:20Z"
    assert result["proposals"][0]["event_time_utc"] is None
    assert "must not be treated as instructions" in result["caveats"][-1]
    assert "body" not in result["proposals"][0]
    assert "body" not in body["query"]


def test_cache_is_bounded_and_returns_latest_first():
    calls = []
    rows = [proposal("new", 1_700_000_100), proposal("old", 1_700_000_000)]
    client = client_with(payload(rows), calls)
    first = client.proposals(SPACE)
    second = client.proposals(SPACE)
    assert len(calls) == 1
    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert [p["id"] for p in first["proposals"]] == ["new", "old"]


@pytest.mark.parametrize("space,limit", [("../x", 1), (SPACE, 0), (SPACE, 26), (SPACE, True)])
def test_invalid_space_or_limit_stops_before_request(space, limit):
    client = Client(opener=lambda *a, **k: pytest.fail("network must not be called"))
    with pytest.raises(ValueError):
        client.proposals(space, limit=limit)


@pytest.mark.parametrize("row,match", [
    (proposal(space="other.eth"), "IDENTITY_MISMATCH"),
    ({**proposal(), "created": "1700000000"}, "MALFORMED_DATA"),
    ({**proposal(), "start": 1, "end": 0}, "MALFORMED_DATA"),
    ({**proposal(), "state": None}, "MALFORMED_DATA"),
])
def test_invalid_identity_or_timestamps_fail_closed(row, match):
    with pytest.raises(Error) as exc:
        module._normalize(payload([row]), SPACE, 10)
    assert exc.value.status == match


def test_duplicate_or_not_newest_first_rows_fail_closed():
    with pytest.raises(Error, match="newest-first"):
        module._normalize(payload([proposal("new", 1_700_000_100), proposal("old", 1_700_000_200)]), SPACE, 10)
    with pytest.raises(Error) as exc:
        module._normalize(payload([proposal("same"), proposal("same", 1_699_999_999)]), SPACE, 10)
    assert exc.value.status == "IDENTITY_MISMATCH"


def test_graphql_errors_and_malformed_responses_are_not_no_observation():
    with pytest.raises(Error) as exc:
        module._normalize({"errors": [{"message": "bad"}], "data": {"proposals": []}}, SPACE, 10)
    assert exc.value.status == "PROVIDER_ERROR"
    with pytest.raises(Error) as exc:
        module._normalize({"data": {"proposals": None}}, SPACE, 10)
    assert exc.value.status == "MALFORMED_DATA"


def test_http_auth_payment_rate_timeout_size_redirect_and_malformed_mapping():
    for status, expected in [(401, "UNAUTHORIZED"), (402, "PAYMENT_REQUIRED"), (403, "FORBIDDEN"), (429, "RATE_LIMITED")]:
        def fail(request, timeout, status=status):
            raise urllib.error.HTTPError(module.ENDPOINT, status, "x", {}, None)
        with pytest.raises(Error) as exc:
            module._request(SPACE, 1, opener=fail)
        assert exc.value.status == expected
    with pytest.raises(Error) as exc:
        module._request(SPACE, 1, opener=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert exc.value.status == "TIMEOUT"
    with pytest.raises(Error) as exc:
        module._request(SPACE, 1, opener=lambda *a, **k: Response({"data": {}}, url="https://evil.example/graphql"))
    assert exc.value.status == "UNSAFE_REDIRECT"
    with pytest.raises(Error) as exc:
        module._request(SPACE, 1, max_response_bytes=1, opener=lambda *a, **k: Response(payload([])))
    assert exc.value.status == "RESPONSE_TOO_LARGE"
    with pytest.raises(Error) as exc:
        module._request(SPACE, 1, opener=lambda *a, **k: Response({"broken": float("nan")}))
    assert exc.value.status == "MALFORMED_DATA"


def test_routing_contract_and_retained_smoke_are_bound_and_minimized():
    contract = json.loads((ROOT / ".codex/snapshot-governance-routing.json").read_text(encoding="utf-8-sig"))
    smoke = json.loads((ROOT / "research-runs/provider-smokes/snapshot-hub-yam-proposals-2026-10-17.json").read_text(encoding="utf-8-sig"))
    index = json.loads((ROOT / ".codex/data-provider-index.json").read_text(encoding="utf-8-sig"))
    route = next(row for row in index["task_routes"][0]["source_priority"]
                 if row["provider"] == "snapshot_hub_graphql")
    assert contract["provider"] == smoke["provider"] == route["provider"]
    assert contract["access"]["smoke_evidence"]["response_sha256"] == smoke["response_sha256"]
    assert smoke["proposals"][0]["space_id"] == smoke["requested_space_id"] == "yam.eth"
    assert "body" not in smoke["proposals"][0]
    assert smoke["latest_proposal_creation_freshness"] == "STALE"

\n