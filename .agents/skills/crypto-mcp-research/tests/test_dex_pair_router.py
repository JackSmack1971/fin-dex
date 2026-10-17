import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / ".agents/skills/crypto-mcp-research/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("dex_pair_router", SCRIPTS / "dex_pair_router.py")
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

DexPairResearchClient = module.DexPairResearchClient
DS_ERROR = module.DexScreenerError
DP_ERROR = module.DexPaprikaError
PAIR = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"


class Primary:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, []

    def pair(self, chain, pair):
        self.calls.append((chain, pair))
        if self.error:
            raise self.error
        return self.value

    def token_pairs(self, chain, token):
        self.calls.append((chain, token))
        if self.error:
            raise self.error
        return self.value


class Secondary:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, []

    def pool_snapshot(self, network, pool):
        self.calls.append((network, pool))
        if self.error:
            raise self.error
        return self.value

    def token_pools(self, network, token, *, limit):
        self.calls.append((network, token, limit))
        if self.error:
            raise self.error
        return self.value


def paprika_result():
    return {
        "provider": "dexpaprika",
        "upstream_origin": "dexpaprika_public_indexer",
        "endpoint": "GET /networks/{network}/pools/{pool_address}",
        "source_url": "https://api.dexpaprika.com/networks/ethereum/pools/" + PAIR,
        "retrieved_at_utc": "2026-10-17T14:00:00Z",
        "caveats": ["timestamps not established"],
        "data": {
            "chain": "ethereum",
            "id": PAIR,
            "dex_id": "uniswap_v3",
            "base_token_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "quote_token_id": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            "last_price_usd": 2500.0,
            "liquidity_usd": 1000000.0,
            "24h": {
                "volume_usd": 500000.0,
                "buy_usd": 300000.0,
                "sell_usd": 200000.0,
                "buys": 120,
                "sells": 80,
                "txns": 200,
                "last_price_usd_change": 2.5,
            },
            "price_time": "2026-10-17T13:59:00Z",
        },
    }


def test_primary_exact_pair_observation_does_not_call_fallback():
    primary = Primary({"pair_count": 1, "provider": "dexscreener"})
    secondary = Secondary()
    result = DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert result["fallback_used"] is False
    assert result["provider_observation"]["provider"] == "dexscreener"
    assert primary.calls == [("ethereum", PAIR)]
    assert secondary.calls == []


@pytest.mark.parametrize("bad_result", [None, {}, {"pair_count": True}, {"pair_count": -1}])
def test_malformed_primary_envelope_does_not_trigger_fallback(bad_result):
    secondary = Secondary(paprika_result())
    with pytest.raises(DS_ERROR, match="MALFORMED_DATA"):
        DexPairResearchClient(Primary(bad_result), secondary).pair("ethereum", PAIR)
    assert secondary.calls == []


@pytest.mark.parametrize("status", ["TIMEOUT", "NETWORK_ERROR", "RATE_LIMITED", "NOT_FOUND"])
def test_eligible_primary_failure_uses_one_exact_pool_fallback(status):
    primary = Primary(error=DS_ERROR(status, "fixture"))
    secondary = Secondary(paprika_result())
    result = DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == status
    observation = result["provider_observation"]
    assert observation["provider"] == "dexpaprika"
    assert observation["identity_status"] == "VERIFIED"
    assert observation["requested_pair_address"].lower() == PAIR.lower()
    assert observation["data"]["pairAddress"].lower() == PAIR.lower()
    assert observation["data"]["liquidity"]["usd"] == 1_000_000.0
    assert observation["data"]["volume"]["24h"] == 500_000.0
    assert observation["data"]["txns"]["24h"] == {"buys": 120, "sells": 80}
    assert observation["data"]["priceChange"]["24h"] == 2.5
    assert observation["data"]["buySellVolumeUsd"]["24h"] == {"buys": 300_000.0, "sells": 200_000.0}
    assert observation["data"]["baseToken"]["address"] == paprika_result()["data"]["base_token_id"]
    assert observation["data"]["quoteToken"]["address"] == paprika_result()["data"]["quote_token_id"]
    assert observation["observation_timestamp"] is None
    assert "passed through without conversion" in observation["caveats"][-1]
    assert secondary.calls == [("ethereum", PAIR)]


def test_empty_primary_result_uses_same_pair_address_fallback():
    primary = Primary({"pair_count": 0})
    secondary = Secondary(paprika_result())
    result = DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "NO_OBSERVATION"
    assert secondary.calls == [("ethereum", PAIR)]


def test_solana_exact_pair_fallback_uses_same_case_sensitive_pool_identity():
    pool = "A8nPhpCJqtqHdqUk35Uj9Hy2YsGXFkCZGuNwvkD3k7VC"
    primary = Primary(error=DS_ERROR("TIMEOUT", "fixture"))
    secondary = Secondary({
        "provider": "dexpaprika", "upstream_origin": "dexpaprika_public_indexer",
        "endpoint": "GET /networks/{network}/pools/{pool_address}",
        "source_url": "https://api.dexpaprika.com/networks/solana/pools/" + pool,
        "retrieved_at_utc": "2026-10-17T16:00:00Z", "caveats": [],
        "data": {
            "chain": "solana", "id": pool, "dex_id": "raydium_clmm",
            "base_token_id": "So11111111111111111111111111111111111111112",
            "quote_token_id": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "tokens": [
                {"id": "So11111111111111111111111111111111111111112"},
                {"id": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
            ],
            "last_price_usd": 150.0, "liquidity_usd": 1000.0,
        },
    })
    result = DexPairResearchClient(primary, secondary).pair("solana", pool)
    assert result["fallback_used"] is True
    assert secondary.calls == [("solana", pool)]
    assert result["provider_observation"]["data"]["pairAddress"] == pool


def test_solana_token_pool_fallback_preserves_exact_mint_and_first_page_metadata():
    token = "So11111111111111111111111111111111111111112"
    pool = "A8nPhpCJqtqHdqUk35Uj9Hy2YsGXFkCZGuNwvkD3k7VC"
    row = {
        "chain": "solana", "id": pool, "dex_id": "raydium_clmm",
        "tokens": [
            {"id": token},
            {"id": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
        ],
    }
    secondary = Secondary({
        "provider": "dexpaprika", "upstream_origin": "dexpaprika_public_indexer",
        "endpoint": "GET /networks/{network}/pools/search?token_address={token_address}&limit={limit}",
        "source_url": "https://api.dexpaprika.com/networks/solana/pools/search",
        "requested_token_address": token, "retrieved_at_utc": "2026-10-17T16:00:00Z",
        "coverage_status": "FIRST_PAGE_ONLY", "has_next_page": True,
        "next_cursor": "solana-cursor", "caveats": [],
        "data": {"results": [row], "has_next_page": True, "next_cursor": "solana-cursor"},
    })
    result = DexPairResearchClient(Primary({"pair_count": 0}), secondary).token_pools("solana", token)
    observation = result["provider_observation"]
    assert result["fallback_used"] is True
    assert secondary.calls == [("solana", token, 100)]
    assert observation["data"]["pairs"][0]["pairAddress"] == pool
    assert observation["data"]["pairs"][0]["tokens"][0]["address"] == token
    assert observation["has_next_page"] is True
    assert observation["next_cursor"] == "solana-cursor"


def test_token_pools_uses_dexpaprika_first_page_as_disclosed_fallback():
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    row = {
        "chain": "ethereum", "id": PAIR, "dex_id": "uniswap_v3",
        "tokens": [{"id": token}, {"id": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}],
        "price_usd": 1.0, "liquidity_usd": 1000.0, "volume_usd_24h": 500.0,
    }
    primary = Primary({"pair_count": 0})
    secondary = Secondary({
        "provider": "dexpaprika", "upstream_origin": "dexpaprika_public_indexer",
        "endpoint": "GET /networks/{network}/pools/search?token_address={token_address}&limit={limit}",
        "source_url": "https://api.dexpaprika.com/networks/ethereum/pools/search",
        "requested_token_address": token, "retrieved_at_utc": "2026-10-17T14:00:00Z",
        "coverage_status": "FIRST_PAGE_ONLY", "has_next_page": True,
        "next_cursor": "opaque", "caveats": [],
        "data": {"results": [row], "has_next_page": True, "next_cursor": "opaque"},
    })
    result = DexPairResearchClient(primary, secondary).token_pools("ethereum", token)
    observation = result["provider_observation"]
    assert result["fallback_used"] is True
    assert result["status"] == "RESPONDED"
    assert secondary.calls == [("ethereum", token, 100)]
    assert observation["coverage_status"] == "FIRST_PAGE_ONLY"
    assert observation["has_next_page"] is True
    assert observation["data"]["pairs"][0]["pairAddress"] == PAIR
    assert observation["data"]["pairs"][0]["tokens"][0]["address"] == token
    assert observation["data"]["pairs"][0]["base_quote_orientation"] == "UNVERIFIED"


def test_token_pools_fallbacks_on_eligible_failure_but_not_payment_denial():
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    row = {
        "chain": "ethereum", "id": PAIR, "dex_id": "uniswap_v3",
        "tokens": [{"id": token}, {"id": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"}],
    }
    secondary = Secondary({
        "provider": "dexpaprika", "upstream_origin": "dexpaprika_public_indexer",
        "endpoint": "GET /networks/{network}/pools/search", "source_url": "https://api.dexpaprika.com/",
        "retrieved_at_utc": "2026-10-17T14:00:00Z", "coverage_status": "RESPONSE_PAGE_COMPLETE",
        "has_next_page": False, "next_cursor": None, "caveats": [], "data": {"results": [row]},
    })
    primary = Primary(error=DS_ERROR("TIMEOUT", "fixture"))
    result = DexPairResearchClient(primary, secondary).token_pools("ethereum", token)
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "TIMEOUT"
    assert secondary.calls == [("ethereum", token, 100)]

    primary = Primary(error=DS_ERROR("PAYMENT_REQUIRED", "fixture"))
    secondary = Secondary()
    with pytest.raises(DS_ERROR, match="PAYMENT_REQUIRED"):
        DexPairResearchClient(primary, secondary).token_pools("ethereum", token)
    assert secondary.calls == []


@pytest.mark.parametrize("status", ["UNAUTHORIZED", "FORBIDDEN", "PAYMENT_REQUIRED", "IDENTITY_MISMATCH", "MALFORMED_DATA"])
def test_primary_auth_payment_and_integrity_errors_never_fall_back(status):
    primary = Primary(error=DS_ERROR(status, "fixture"))
    secondary = Secondary(paprika_result())
    with pytest.raises(DS_ERROR):
        DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert secondary.calls == []


def test_unknown_chain_and_dexpaprika_quota_error_are_preserved():
    primary = Primary(error=DS_ERROR("TIMEOUT", "fixture"))
    secondary = Secondary(error=DP_ERROR("PAYMENT_REQUIRED", "anonymous quota exhausted"))
    with pytest.raises(DP_ERROR, match="PAYMENT_REQUIRED"):
        DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert secondary.calls == [("ethereum", PAIR)]

    primary = Primary(error=DS_ERROR("TIMEOUT", "fixture"))
    secondary = Secondary(paprika_result())
    with pytest.raises(DS_ERROR, match="FALLBACK_UNSUPPORTED_CHAIN"):
        DexPairResearchClient(primary, secondary).pair("unknown-chain", PAIR)
    assert secondary.calls == []


def test_fallback_uses_at_most_one_request_per_provider():
    primary = Primary(error=DS_ERROR("HTTP_ERROR", "HTTP 503; provider branch stopped"))
    secondary = Secondary(paprika_result())
    DexPairResearchClient(primary, secondary).pair("ethereum", PAIR)
    assert len(primary.calls) == 1
    assert len(secondary.calls) == 1


def test_pool_ohlcv_is_available_through_shared_router_with_explicit_direct_route():
    candles = [{"time_open": "2026-10-17T00:00:00Z", "time_close": "2026-10-17T01:00:00Z",
                "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]

    class Paprika:
        def __init__(self):
            self.calls = []

        def pool_ohlcv(self, network, address, **kwargs):
            self.calls.append((network, address, kwargs))
            return {"provider": "dexpaprika", "upstream_origin": "dexpaprika_public_indexer",
                    "endpoint": "GET /networks/{network}/pools/{pool_address}/ohlcv",
                    "source_url": "https://api.dexpaprika.com/example", "retrieved_at_utc": "2026-10-17T01:00:00Z",
                    "identity_status": "REQUEST_ROUTE_MATCHED_RESPONSE_HAS_NO_POOL_ENVELOPE",
                    "coverage_status": "BOUNDED_24H_RESPONSE", "candle_interval": "1h",
                    "candle_count": 1, "data": candles, "caveats": []}

    paprika = Paprika()
    result = DexPairResearchClient(dexpaprika=paprika).pool_ohlcv("ethereum", PAIR)
    assert paprika.calls == [("ethereum", PAIR, {"start": "-24h", "interval": "1h", "limit": 24})]
    observation = result["provider_observation"]
    assert result["status"] == "RESPONDED"
    assert observation["data"]["candles"] == candles
    assert "not a fallback observation" in observation["caveats"][-1]
