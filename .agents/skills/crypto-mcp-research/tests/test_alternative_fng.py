import datetime as dt
import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/alternative_fng.py"
SPEC = importlib.util.spec_from_file_location("alternative_fng", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASE_URL = module.BASE_URL
AlternativeFNGClient = module.AlternativeFNGClient
ProviderError = module.ProviderError
_request = module._request


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body


def fixture_payload(values=(55, 48), timestamps=(1792195200, 1792108800)):
    return {
        "name": "Fear and Greed Index",
        "data": [
            {"value": str(value), "value_classification": "Greed" if value > 50 else "Fear", "timestamp": str(timestamp)}
            for value, timestamp in zip(values, timestamps)
        ],
        "metadata": {"error": None},
    }


def test_fixed_free_history_request_and_timestamp_normalization():
    seen = []
    payload = fixture_payload()

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(json.dumps(payload).encode())

    fixed_now = dt.datetime(2026, 10, 17, 14, tzinfo=dt.timezone.utc)
    client = AlternativeFNGClient(opener=opener, utc_clock=lambda: fixed_now)
    result = client.history(2)
    assert seen[0].full_url == BASE_URL + "/fng/?limit=2&format=json"
    assert seen[0].method == "GET"
    assert result["endpoint"] == "GET /fng/"
    assert result["retrieved_at_utc"] == "2026-10-17T14:00:00Z"
    assert result["observations"][0]["value"] == 55
    assert result["observations"][0]["unit"] == "index_points_0_to_100"
    assert result["observations"][0]["observed_at_utc"] == "2026-10-17T00:00:00Z"
    assert result["raw_response"] == payload
    assert result["attribution_required"] is True
    assert result["attribution_text"] == "Data from alternative.me"


def test_stale_latest_record_is_flagged():
    payload = fixture_payload(timestamps=(1791936000, 1791849600))
    client = AlternativeFNGClient(
        opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()),
        utc_clock=lambda: dt.datetime(2026, 10, 17, 14, tzinfo=dt.timezone.utc),
    )
    assert client.history(2)["latest_observation_freshness"] == "STALE"


@pytest.mark.parametrize("limit", [0, -1, 32, True])
def test_invalid_history_limit_rejected_without_network(limit):
    client = AlternativeFNGClient(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.history(limit)


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (402, "PAYMENT_REQUIRED"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED")
])
def test_auth_payment_not_found_and_rate_limit_are_classified(code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as error:
        _request(1, opener=opener)
    assert error.value.status == status
    assert len(calls) == 1


def test_timeout_is_classified_without_retry():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request(1, opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    [], {}, {"name": "wrong", "data": [], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "101", "timestamp": "1792195200", "value_classification": "Greed"}], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "55", "timestamp": "oops", "value_classification": "Greed"}], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "55", "timestamp": "1792195200", "value_classification": "Greed"}, {"value": "54", "timestamp": "1792195200", "value_classification": "Greed"}], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "55", "timestamp": "1792108800", "value_classification": "Greed"}, {"value": "54", "timestamp": "1792195200", "value_classification": "Greed"}], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "55", "timestamp": "1792195200"}], "metadata": {"error": None}},
    {"name": "Fear and Greed Index", "data": [{"value": "55", "timestamp": "1792195200", "value_classification": "Greed"}], "metadata": {"error": "provider failure"}},
])
def test_malformed_or_contradictory_api_payload_is_rejected(payload):
    client = AlternativeFNGClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        client.history(7)


def test_malformed_json_and_oversized_body_are_rejected():
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        _request(1, opener=lambda request, timeout: FakeResponse(b"not json"))
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request(1, max_response_bytes=2, opener=lambda request, timeout: FakeResponse(b"123"))


def test_cache_is_process_local_and_respects_ttl():
    payload = fixture_payload()
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(json.dumps(payload).encode())

    tick = [0.0]
    client = AlternativeFNGClient(opener=opener, clock=lambda: tick[0], cache_ttl_seconds=20)
    assert client.history(2)["cache"] == "miss"
    assert client.history(2)["cache"] == "hit"
    tick[0] = 21
    assert client.history(2)["cache"] == "miss"
    assert len(calls) == 2


def test_sentiment_is_composite_context_not_independent_market_evidence():
    result = AlternativeFNGClient(
        opener=lambda request, timeout: FakeResponse(json.dumps(fixture_payload()).encode())
    ).history(2)
    assert result["upstream_origin"] == "alternative_me_composite_fear_greed_index"
    assert "not an asset-specific signal" in result["interpretation"]


def test_saved_smoke_binds_live_read_to_free_endpoint_docs_and_attribution():
    policy = json.loads((ROOT / ".codex/alternative-fng-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8-sig"))
    assert policy["access"]["runtime_readiness"] == "QUALIFIED"
    assert policy["access"]["auth"] == "none"
    assert policy["access"]["attribution_required"] is True
    assert smoke["endpoint"] == "GET /fng/"
    assert smoke["retrieved_at_utc"] == "2026-10-17T13:58:14Z"
    assert len(smoke["observations"]) == 7
    assert smoke["observations"][0]["observed_at_utc"] == "2026-10-17T00:00:00Z"
    assert smoke["attribution_required"] is True
    assert "Data from alternative.me" in smoke["attribution_text"]
