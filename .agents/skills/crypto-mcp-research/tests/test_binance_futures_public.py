import importlib.util
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/binance_futures_public.py"
SPEC = importlib.util.spec_from_file_location("binance_futures_public", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BinanceFuturesClient = module.BinanceFuturesClient
ProviderError = module.ProviderError
_request = module._request


class FakeResponse:
    status = 200

    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.body


def rows(symbol="ETHUSDT", timestamps=None):
    timestamps = timestamps or [
        int(dt.datetime(2026, 10, 17, hour, tzinfo=dt.timezone.utc).timestamp() * 1000)
        for hour in (15, 16)
    ]
    return [
        {"symbol": symbol, "sumOpenInterest": "123.45", "sumOpenInterestValue": "456789.01", "timestamp": ts}
        for ts in timestamps
    ]


def test_exact_symbol_route_is_anonymous_bounded_get_and_units_are_preserved():
    seen = []
    client = BinanceFuturesClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(rows()).encode())),
        cache_ttl_seconds=0,
        utc_clock=lambda: "2026-10-17T16:00:00Z",
    )
    result = client.open_interest_history("ETHUSDT", "1h", 2)
    request = seen[0]
    assert request.full_url.startswith("https://fapi.binance.com/futures/data/openInterestHist?")
    assert "symbol=ETHUSDT" in request.full_url and "period=1h" in request.full_url and "limit=2" in request.full_url
    assert request.method == "GET"
    assert request.get_header("Authorization") is None
    assert len(seen) == 1
    assert result["retrieved_at_utc"] == "2026-10-17T16:00:00Z"
    assert result["data"][0]["sumOpenInterest"]["raw"] == "123.45"
    assert "not specified" in result["units"]["sumOpenInterestValue"]
    assert result["freshness_status"] == "FRESH"


@pytest.mark.parametrize("code,status", [(401,"UNAUTHORIZED"),(402,"PAYMENT_REQUIRED"),(403,"FORBIDDEN"),(404,"NOT_FOUND"),(429,"RATE_LIMITED")])
def test_http_errors_are_classified_and_not_retried(code, status):
    seen = []
    def opener(request, timeout):
        seen.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)
    with pytest.raises(ProviderError) as exc:
        _request("ETHUSDT", "1h", 1, opener=opener)
    assert exc.value.status == status
    assert len(seen) == 1


def test_timeout_and_response_size_are_bounded():
    seen = []
    def timeout(request, timeout):
        seen.append(request)
        raise TimeoutError("fixture")
    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request("ETHUSDT", "1h", 1, opener=timeout)
    assert len(seen) == 1
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request("ETHUSDT", "1h", 1,
                 opener=lambda *args, **kwargs: FakeResponse(b" " * 101), max_response_bytes=100)


def test_http_451_is_not_retried_and_stale_latest_rows_are_labeled():
    calls = []
    def denied(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 451, "region fixture", {}, None)
    with pytest.raises(ProviderError) as exc:
        _request("ETHUSDT", "1h", 1, opener=denied)
    assert exc.value.status == "REGION_UNAVAILABLE"
    assert len(calls) == 1
    old_rows = rows(timestamps=[1_700_000_000_000])
    result = BinanceFuturesClient(
        opener=lambda *a, **k: FakeResponse(json.dumps(old_rows).encode()),
        cache_ttl_seconds=0,
        utc_clock=lambda: "2026-10-17T16:00:00Z",
    ).open_interest_history("ETHUSDT", "1h", 1)
    assert result["freshness_status"] == "STALE"


@pytest.mark.parametrize("symbol,period,limit", [("ethusdt", "1h", 2), ("ETHUSDT", "3m", 2), ("ETHUSDT", "1h", 101), ("ETH/USDT", "1h", 1)])
def test_invalid_route_parameters_fail_before_network(symbol, period, limit):
    called = []
    client = BinanceFuturesClient(opener=lambda *args, **kwargs: called.append(True))
    with pytest.raises(ValueError):
        client.open_interest_history(symbol, period, limit)
    assert not called


def test_identity_mismatch_malformed_rows_and_nonfinite_values_fail_closed():
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(json.dumps(rows("BTCUSDT")).encode()), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 2)
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(b"not-json"), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 2)
    bad = rows()
    bad[0]["sumOpenInterest"] = "NaN"
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(json.dumps(bad).encode()), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 2)


def test_duplicate_timestamps_and_excess_rows_are_rejected():
    duplicate = rows(timestamps=[1000, 1000])
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(json.dumps(duplicate).encode()), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 2)
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(json.dumps(rows(timestamps=[1000, 2000, 3000])).encode()), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 2)


def test_future_timestamps_are_rejected():
    future = rows(timestamps=[int(time.time() * 1000) + 86_400_000])
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        BinanceFuturesClient(opener=lambda *a, **k: FakeResponse(json.dumps(future).encode()), cache_ttl_seconds=0).open_interest_history("ETHUSDT", "1h", 1)


def test_cache_is_short_and_keyed_by_exact_query():
    calls = []
    client = BinanceFuturesClient(
        opener=lambda request, timeout: (calls.append(request.full_url) or FakeResponse(json.dumps(rows("BTCUSDT" if "BTCUSDT" in request.full_url else "ETHUSDT")).encode())),
        clock=lambda: 10.0,
        utc_clock=lambda: "2026-10-17T16:00:00Z",
    )
    first = client.open_interest_history("ETHUSDT", "1h", 2)
    second = client.open_interest_history("ETHUSDT", "1h", 2)
    third = client.open_interest_history("BTCUSDT", "1h", 2)
    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert third["requested_symbol"] == "BTCUSDT"
    assert len(calls) == 2
