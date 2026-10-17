import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/dexpaprika_public.py"
SPEC = importlib.util.spec_from_file_location("dexpaprika_public", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASE_URL = module.BASE_URL
DexPaprikaClient = module.DexPaprikaClient
ProviderError = module.ProviderError
_request = module._request
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"


@pytest.fixture(autouse=True)
def qualified_offline_route_contract(tmp_path, monkeypatch):
    contract = tmp_path / "dexpaprika-routing.json"
    contract.write_text(json.dumps({"access": {"keyless_runtime_status": "READY_ZERO_CHARGE_REQUALIFIED"}}),
                        encoding="utf-8")
    monkeypatch.setattr(module, "ROUTE_CONTRACT", contract)


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


def pool_fixture():
    return json.loads((ROOT / "research-runs/provider-smokes/dexpaprika-public-api-ethereum-usdc-weth.json").read_text(encoding="utf-8"))["data"]


def test_public_pool_route_uses_fixed_anonymous_get_and_preserves_timestamp_scope():
    seen = []
    payload = pool_fixture()

    def opener(request, timeout):
        seen.append((request, timeout))
        return FakeResponse(json.dumps(payload).encode())

    client = DexPaprikaClient(opener=opener, utc_clock=lambda: "2026-10-17T14:20:00Z")
    result = client.pool_snapshot("ethereum", POOL)
    request = seen[0][0]
    assert request.full_url == f"{BASE_URL}/networks/ethereum/pools/{POOL}"
    assert request.method == "GET"
    assert request.get_header("Authorization") is None
    assert result["provider"] == "dexpaprika"
    assert result["retrieved_at_utc"] == "2026-10-17T14:20:00Z"
    assert result["observation_timestamps"]["price_time"] == "2026-10-17T14:16:35Z"
    assert "not assumed to timestamp liquidity" in result["caveats"][0]
    assert result["data"]["liquidity_usd"] == 82331518.03228185


def test_exhausted_route_status_blocks_all_transports_before_request(tmp_path, monkeypatch):
    contract = tmp_path / "dexpaprika-routing.json"
    contract.write_text(json.dumps({"access": {"keyless_runtime_status": "UNAVAILABLE_KEYLESS_QUOTA_EXHAUSTED"}}),
                        encoding="utf-8")
    monkeypatch.setattr(module, "ROUTE_CONTRACT", contract)
    calls = []
    client = DexPaprikaClient(opener=lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ProviderError, match="ROUTE_UNAVAILABLE"):
        client.pool_snapshot("ethereum", POOL)

    assert calls == []


def test_token_pool_search_is_fixed_bounded_anonymous_and_identity_checked():
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    payload = {
        "results": [{
            "chain": "ethereum", "id": POOL, "dex_id": "uniswap_v3",
            "tokens": [{"id": token}, {"id": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}],
            "price_usd": 1.0, "liquidity_usd": 1000.0, "volume_usd_24h": 500.0,
        }],
        "has_next_page": True, "next_cursor": "opaque-cursor",
    }
    seen = []
    client = DexPaprikaClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(payload).encode())),
        utc_clock=lambda: "2026-10-17T14:20:00Z",
    )
    result = client.token_pools("ethereum", token)
    assert seen[0].full_url == f"{BASE_URL}/networks/ethereum/pools/search?token_address={token}&limit=100&detailed=true"
    assert seen[0].method == "GET"
    assert seen[0].get_header("Authorization") is None
    assert result["pair_count"] == 1
    assert result["coverage_status"] == "FIRST_PAGE_ONLY"
    assert result["next_cursor"] == "opaque-cursor"
    assert result["data"]["results"][0]["id"] == POOL


def test_cli_exposes_bounded_token_pool_discovery(monkeypatch, capsys):
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    calls = []

    class Client:
        def token_pools(self, network, token_address, *, limit):
            calls.append((network, token_address, limit))
            return {"provider": "dexpaprika", "pair_count": 0, "data": {"results": []}}

    monkeypatch.setattr(module, "DexPaprikaClient", Client)
    assert module.main(["token-pools", "--network", "ethereum", "--token-address", token]) == 0
    output = json.loads(capsys.readouterr().out)
    assert calls == [("ethereum", token, 100)]
    assert output["provider"] == "dexpaprika"
    assert output["pair_count"] == 0


def test_solana_pool_and_mint_routes_accept_base58_public_keys_and_keep_case_sensitive_identity():
    pool = "A8nPhpCJqtqHdqUk35Uj9Hy2YsGXFkCZGuNwvkD3k7VC"
    wrapped_sol = "So11111111111111111111111111111111111111112"
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    pool_payload = {
        "chain": "solana", "id": pool, "dex_id": "raydium_clmm",
        "tokens": [{"id": wrapped_sol}, {"id": usdc}],
        "base_token_id": wrapped_sol, "quote_token_id": usdc,
        "last_price_usd": 150.0, "liquidity_usd": 1000.0,
    }
    seen = []
    client = DexPaprikaClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(pool_payload).encode())),
        utc_clock=lambda: "2026-10-17T16:00:00Z",
    )
    result = client.pool_snapshot("solana", pool)
    assert seen[0].full_url == f"{BASE_URL}/networks/solana/pools/{pool}"
    assert result["data"]["chain"] == "solana"

    token_payload = {
        "results": [{
            "chain": "solana", "id": pool, "dex_id": "raydium_clmm",
            "tokens": [{"id": wrapped_sol}, {"id": usdc}],
        }],
        "has_next_page": False, "next_cursor": None,
    }
    seen.clear()
    client = DexPaprikaClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(token_payload).encode())),
    )
    result = client.token_pools("solana", usdc)
    assert seen[0].full_url == f"{BASE_URL}/networks/solana/pools/search?token_address={usdc}&limit=100&detailed=true"
    assert result["data"]["results"][0]["tokens"][1]["id"] == usdc

    token_payload["results"][0]["tokens"][1]["id"] = usdc[:-1] + "2"
    client = DexPaprikaClient(opener=lambda request, timeout: FakeResponse(json.dumps(token_payload).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_pools("solana", usdc)


def test_token_pool_search_rejects_wrong_token_network_and_unbounded_limit():
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    client = DexPaprikaClient(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    for kwargs in ({"limit": 101}, {"limit": True}):
        with pytest.raises(ValueError):
            client.token_pools("ethereum", token, **kwargs)

    payload = {"results": [{
        "chain": "base", "id": POOL,
        "tokens": [{"id": token}, {"id": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}],
    }]}
    client = DexPaprikaClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_pools("ethereum", token)
    payload["results"][0]["chain"] = "ethereum"
    payload["results"][0]["tokens"][0]["id"] = "0x" + "4" * 40
    client = DexPaprikaClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_pools("ethereum", token)


@pytest.mark.parametrize("path", [
    "/protocols", "//attacker.invalid/networks/ethereum/pools/0x" + "1" * 40,
    "/networks/ethereum/pools/not-an-address", "/networks/ethereum/pools/0x" + "1" * 40 + "/transactions",
])
def test_only_exact_pool_details_route_is_callable(path):
    with pytest.raises(ValueError):
        _request(path, opener=lambda *args, **kwargs: pytest.fail("network should not be called"))


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (402, "PAYMENT_REQUIRED"), (403, "FORBIDDEN"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED"),
])
def test_auth_payment_not_found_and_rate_limit_failures_are_distinct(code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as error:
        _request(f"/networks/ethereum/pools/{POOL}", opener=opener)
    assert error.value.status == status
    assert len(calls) == 1


def test_timeout_is_distinct_and_not_retried():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request(f"/networks/ethereum/pools/{POOL}", opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not json", b"[]", b"NaN", b'{"x":1e999}'])
def test_malformed_or_nonfinite_responses_are_rejected(body):
    with pytest.raises(ProviderError):
        _request(f"/networks/ethereum/pools/{POOL}", opener=lambda *args, **kwargs: FakeResponse(body))


def test_response_size_is_bounded():
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request(f"/networks/ethereum/pools/{POOL}",
                 opener=lambda *args, **kwargs: FakeResponse(b" " * 101), max_response_bytes=100)


def test_network_pool_and_token_identity_are_checked_before_returning():
    calls = []
    payload = pool_fixture()
    payload["id"] = "0x" + "1" * 40
    client = DexPaprikaClient(opener=lambda *args, **kwargs: (calls.append(args) or FakeResponse(json.dumps(payload).encode())))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.pool_snapshot("ethereum", POOL)
    assert len(calls) == 1

    payload = pool_fixture()
    payload["quote_token_id"] = "0x" + "1" * 40
    client = DexPaprikaClient(opener=lambda *args, **kwargs: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError, match="base/quote contracts"):
        client.pool_snapshot("ethereum", POOL)


def test_keyless_pool_ohlcv_uses_fixed_free_plan_window_and_preserves_candles():
    candles = [{"time_open": "2026-10-16T14:00:00Z", "time_close": "2026-10-16T15:00:00Z",
                "open": 2500.0, "high": 2510.0, "low": 2490.0, "close": 2505.0, "volume": 1000.0}]
    seen = []
    client = DexPaprikaClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(candles).encode())),
        utc_clock=lambda: "2026-10-17T14:20:00Z",
    )
    result = client.pool_ohlcv("ethereum", POOL)
    assert seen[0].full_url == f"{BASE_URL}/networks/ethereum/pools/{POOL}/ohlcv?start=-24h&interval=1h&limit=24"
    assert seen[0].method == "GET"
    assert seen[0].get_header("Authorization") is None
    assert result["retrieved_at_utc"] == "2026-10-17T14:20:00Z"
    assert result["candle_count"] == 1
    assert result["data"] == candles
    assert "pool-specific" in result["caveats"][2]


@pytest.mark.parametrize("kwargs", [
    {"start": "-7d"}, {"interval": "10m"}, {"interval": "24h"}, {"limit": 25}, {"limit": True},
])
def test_keyless_ohlcv_rejects_out_of_tier_scope_before_network(kwargs):
    client = DexPaprikaClient(opener=lambda *args, **kw: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.pool_ohlcv("ethereum", POOL, **kwargs)


@pytest.mark.parametrize("body", [b"{}", b"[{}]", b'[{"time_open":"x","time_close":"y","open":1,"high":1,"low":1,"close":NaN,"volume":1}]'])
def test_keyless_ohlcv_rejects_malformed_response(body):
    client = DexPaprikaClient(opener=lambda *args, **kw: FakeResponse(body))
    with pytest.raises(ProviderError):
        client.pool_ohlcv("ethereum", POOL)


def test_invalid_network_or_pool_does_not_call_provider():
    calls = []
    client = DexPaprikaClient(opener=lambda *args, **kwargs: calls.append(args))
    solana_pool = "A8nPhpCJqtqHdqUk35Uj9Hy2YsGXFkCZGuNwvkD3k7VC"
    for network, pool in [
        ("", POOL), ("../ethereum", POOL), ("ethereum", "bad"),
        ("ethereum", "https://x.invalid"), ("ethereum", solana_pool),
        ("solana", "not-a-valid-public-key"),
    ]:
        with pytest.raises(ValueError):
            client.pool_snapshot(network, pool)
    assert calls == []


def test_cache_is_process_local_and_expires():
    calls = []
    payload = pool_fixture()
    tick = [0]
    client = DexPaprikaClient(
        opener=lambda *args, **kwargs: (calls.append(args) or FakeResponse(json.dumps(payload).encode())),
        clock=lambda: tick[0],
    )
    assert client.pool_snapshot("ethereum", POOL)["cache"] == "miss"
    assert client.pool_snapshot("ethereum", POOL)["cache"] == "hit"
    tick[0] = 31
    assert client.pool_snapshot("ethereum", POOL)["cache"] == "miss"
    assert len(calls) == 2


def test_saved_smoke_proves_documented_keyless_route_only_for_exact_pool():
    artifact = json.loads((ROOT / "research-runs/provider-smokes/dexpaprika-public-api-ethereum-usdc-weth.json").read_text(encoding="utf-8"))
    client_smoke = json.loads((ROOT / "research-runs/provider-smokes/dexpaprika-client-ethereum-usdc-weth.json").read_text(encoding="utf-8"))
    contract = json.loads((ROOT / ".codex/dexpaprika-routing.json").read_text(encoding="utf-8"))
    assert artifact["http_status"] == 200
    assert artifact["authorization_header_sent"] is False
    assert artifact["source_url"] == contract["smoke_evidence"]["source_url"]
    assert artifact["retrieved_at_utc"] == "2026-10-17T14:19:16Z"
    assert contract["smoke_evidence"]["artifact"] == "research-runs/provider-smokes/dexpaprika-client-ethereum-usdc-weth.json"
    assert client_smoke["retrieved_at_utc"] == "2026-10-17T14:21:15Z"
    assert client_smoke["endpoint"] == "GET /networks/{network}/pools/{pool_address}"
    assert client_smoke["data"]["id"].lower() == POOL.lower()
    assert client_smoke["data"]["chain"] == "ethereum"
    assert client_smoke["data"]["liquidity_usd"] == 82322075.63242704
    assert client_smoke["data"]["24h"]["volume_usd"] == 27074443.82718024
    assert contract["access"]["runtime_readiness"].startswith("PARTIALLY_QUALIFIED")
    assert "Ethereum Uniswap V3 USDC/WETH only" in contract["supported_routes"][0]["scope"]
    token_search = next(route for route in contract["supported_routes"] if route["task"] == "bounded exact-token pool discovery")
    assert token_search["tool"].endswith("--limit 100")
    assert "preserves has_next_page and next_cursor" in token_search["documented_keyless_scope"]
    assert token_search["readiness"] == "IMPLEMENTED_FIXTURE_TESTED; no exact-token search live smoke is retained, and current anonymous quota exhaustion prevents live requalification."
    assert contract["independence"]["status"] == "UNKNOWN"
    assert contract["request_policy"]["documented_keyless_allowance"].startswith("CONFLICTED:")
    assert "50,000 free requests/month per IP" in contract["request_policy"]["documented_keyless_allowance"]
    assert "100,000 monthly credits" in contract["request_policy"]["documented_keyless_allowance"]
    assert contract["access"]["keyless_runtime_status"] == "UNAVAILABLE_KEYLESS_QUOTA_EXHAUSTED"
    assert "READY_ZERO_CHARGE_REQUALIFIED" in contract["request_policy"]["runtime_call_gate"]
    dex_contract = json.loads((ROOT / ".codex/dexscreener-routing.json").read_text(encoding="utf-8"))
    assert "skip the fallback" in dex_contract["request_policy"]["fallback_policy"]
    assert "billing path is unverified" in dex_contract["request_policy"]["fallback_policy"]
    assert contract["documented_freshness"]["status"] == "CONFLICTED"
    assert len(contract["documented_freshness"]["claims"]) == 2
    assert "do not infer liquidity/volume observation time" in contract["documented_freshness"]["handling"]
