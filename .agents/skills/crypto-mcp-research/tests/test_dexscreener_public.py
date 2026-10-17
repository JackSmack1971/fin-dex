import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/dexscreener_public.py"
SPEC = importlib.util.spec_from_file_location("dexscreener_public", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASE_URL = module.BASE_URL
DexScreenerClient = module.DexScreenerClient
ProviderError = module.ProviderError
_request = module._request

TOKEN = "0x1111111111111111111111111111111111111111"
PAIR = "0x2222222222222222222222222222222222222222"


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


def pair_row(chain="ethereum", token=TOKEN, pair_address=PAIR):
    return {
        "chainId": chain,
        "dexId": "fixture-dex",
        "pairAddress": pair_address,
        "baseToken": {"address": token, "name": "Fixture", "symbol": "FIX"},
        "quoteToken": {"address": "0x3333333333333333333333333333333333333333", "name": "USD Coin", "symbol": "USDC"},
        "priceUsd": "1.25",
        "volume": {"h24": 1000.0},
        "liquidity": {"usd": 50000.0, "base": 20000.0, "quote": 30000.0},
        "pairCreatedAt": 1700000000000,
    }


def test_token_pairs_fixed_host_exact_identity_and_timestamp_caveat():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(json.dumps([pair_row()]).encode())

    client = DexScreenerClient(opener=opener, utc_clock=lambda: "2026-10-17T14:00:00Z")
    result = client.token_pairs("ethereum", TOKEN)
    assert seen[0].full_url == f"{BASE_URL}/token-pairs/v1/ethereum/{TOKEN}"
    assert seen[0].method == "GET"
    assert result["requested_chain_id"] == "ethereum"
    assert result["requested_identifier"] == TOKEN
    assert result["retrieved_at_utc"] == "2026-10-17T14:00:00Z"
    assert result["observation_timestamp"] is None
    assert "pairCreatedAt is only the pair creation time" in result["observation_timestamp_note"]
    assert result["data"][0]["liquidity"]["usd"] == 50000.0


def test_pair_route_preserves_original_envelope():
    payload = {"schemaVersion": "1.0.0", "pairs": [pair_row()]}
    client = DexScreenerClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    result = client.pair("ethereum", PAIR)
    assert result["endpoint"] == "GET /latest/dex/pairs/{chainId}/{pairId}"
    assert result["data"] == payload


@pytest.mark.parametrize("bad_chain", ["", "Ethereum", "../ethereum", "https://other.example"])
def test_invalid_chain_identity_rejected_before_network(bad_chain):
    client = DexScreenerClient(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.token_pairs(bad_chain, TOKEN)


def test_provider_chain_mismatch_is_rejected():
    client = DexScreenerClient(opener=lambda request, timeout: FakeResponse(json.dumps([pair_row(chain="base")]).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_pairs("ethereum", TOKEN)


def test_provider_token_or_pair_mismatch_is_rejected():
    client = DexScreenerClient(opener=lambda request, timeout: FakeResponse(json.dumps([pair_row(token="0x4444444444444444444444444444444444444444")]).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_pairs("ethereum", TOKEN)
    client = DexScreenerClient(opener=lambda request, timeout: FakeResponse(json.dumps({"pairs": [pair_row(pair_address="0x5555555555555555555555555555555555555555")]}).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.pair("ethereum", PAIR)


def test_empty_pair_list_is_missing_observation_not_zero_liquidity():
    client = DexScreenerClient(opener=lambda request, timeout: FakeResponse(b"[]"))
    result = client.token_pairs("ethereum", TOKEN)
    assert result["pair_count"] == 0
    assert result["identity_status"] == "NO_OBSERVATION"
    assert result["freshness_status"] == "NO_OBSERVATION"
    assert result["data"] == []


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
        _request(f"/token-pairs/v1/ethereum/{TOKEN}", opener=opener)
    assert error.value.status == status
    assert len(calls) == 1


def test_timeout_and_malformed_payload_are_not_retried_or_accepted():
    calls = []

    def timeout(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture timeout")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request(f"/token-pairs/v1/ethereum/{TOKEN}", opener=timeout)
    assert len(calls) == 1
    for body in (b"not json", b"null", b"1e999", b'{"no_pairs":[]}'):
        with pytest.raises(ProviderError, match="MALFORMED_DATA"):
            DexScreenerClient(opener=lambda request, timeout, body=body: FakeResponse(body)).token_pairs("ethereum", TOKEN)


def test_only_fixed_read_routes_and_bounded_body_are_allowed():
    for path in ("/orders/v1/ethereum/token", "/latest/dex/search?q=token", "//other.example/pairs"):
        with pytest.raises(ValueError):
            _request(path, opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request(f"/token-pairs/v1/ethereum/{TOKEN}", max_response_bytes=2,
                 opener=lambda request, timeout: FakeResponse(b"[] "))


def test_process_cache_and_disabled_cache():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(json.dumps([pair_row()]).encode())

    tick = [0.0]
    client = DexScreenerClient(opener=opener, clock=lambda: tick[0], cache_ttl_seconds=5)
    assert client.token_pairs("ethereum", TOKEN)["cache"] == "miss"
    assert client.token_pairs("ethereum", TOKEN)["cache"] == "hit"
    tick[0] = 6
    assert client.token_pairs("ethereum", TOKEN)["cache"] == "miss"
    assert len(calls) == 2
    client = DexScreenerClient(opener=opener, cache_ttl_seconds=0)
    client.token_pairs("ethereum", TOKEN)
    client.token_pairs("ethereum", TOKEN)
    assert len(calls) == 4


def test_route_inventory_documents_free_read_scope_and_no_fallback():
    policy = json.loads((ROOT / ".codex/dexscreener-routing.json").read_text(encoding="utf-8"))
    assert policy["access"]["auth"] == "none"
    assert policy["access"]["runtime_readiness"] == "PARTIALLY_QUALIFIED"
    assert policy["routing"][0]["fallback"]["provider"] == "dexpaprika"
    assert "Solana" in policy["routing"][0]["fallback"]["scope"]
    assert "case-sensitive" in policy["routing"][0]["fallback"]["token_search_coverage"]
    assert "token-pools" in policy["routing"][0]["fallback"]["operations"][1]
    assert policy["routing"][0]["fallback"]["independence"].startswith("UNKNOWN")
    assert policy["request_policy"]["max_requests_per_command"] == 2
    assert policy["request_policy"]["max_requests_per_provider_per_command"] == 1
    assert policy["upstream"]["lineage"] == "DEX Screener custom blockchain indexer; FAQ states it parses raw blockchain logs without external APIs."


def test_saved_smoke_is_identity_matched_but_metric_freshness_unverified():
    policy = json.loads((ROOT / ".codex/dexscreener-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8-sig"))
    assert policy["access"]["runtime_readiness"] == "PARTIALLY_QUALIFIED"
    assert smoke["identity_status"] == "VERIFIED"
    assert smoke["freshness_status"] == "UNVERIFIED_NO_OBSERVATION_TIMESTAMP"
    assert smoke["requested_chain_id"] == smoke["data"]["pairs"][0]["chainId"] == "ethereum"
    assert smoke["requested_identifier"].lower() == smoke["data"]["pairs"][0]["pairAddress"].lower()
    assert smoke["retrieved_at_utc"] == "2026-10-17T13:55:38Z"
