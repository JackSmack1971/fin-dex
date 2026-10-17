import importlib.util
import json
import pathlib
import sys
import time
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/hyperliquid_public.py"
SPEC = importlib.util.spec_from_file_location("hyperliquid_public", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASE_URL = module.BASE_URL
HyperliquidClient = module.HyperliquidClient
ProviderError = module.ProviderError
_request = module._request


class FakeResponse:
    status = 200

    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.body


def test_funding_history_is_fixed_anonymous_read_only_post_and_normalized():
    now = int(time.time() * 1000)
    start, end = now - 3_600_000, now
    rows = [
        {"coin": "ETH", "time": end - 3_600_000, "fundingRate": "0.0001", "premium": "0.0002"},
        {"coin": "ETH", "time": end, "fundingRate": "-0.00005"},
    ]
    seen = []
    client = HyperliquidClient(
        opener=lambda request, timeout: (seen.append(request) or FakeResponse(json.dumps(rows).encode())),
        utc_clock=lambda: "2026-10-17T16:00:00Z",
    )
    result = client.funding_history("ETH", start, end)
    request = seen[0]
    assert request.full_url == BASE_URL
    assert request.method == "POST"
    assert request.get_header("Authorization") is None
    assert json.loads(request.data) == {"type": "fundingHistory", "coin": "ETH", "startTime": start, "endTime": end}
    assert result["retrieved_at_utc"] == "2026-10-17T16:00:00Z"
    assert result["data"][0]["funding_rate_decimal"] == 0.0001
    assert result["data"][1]["funding_rate_raw"] == "-0.00005"
    assert result["data"][0]["premium_raw"] == "0.0002"
    assert result["unit"].endswith("not annualized")


@pytest.mark.parametrize("code,status", [(401,"UNAUTHORIZED"),(402,"PAYMENT_REQUIRED"),(403,"FORBIDDEN"),(404,"NOT_FOUND"),(429,"RATE_LIMITED")])
def test_http_errors_are_classified_and_not_retried(code,status):
    seen=[]
    def opener(request, timeout):
        seen.append(request)
        raise urllib.error.HTTPError(request.full_url,code,"fixture",{},None)
    with pytest.raises(ProviderError) as exc:
        _request({"type":"fundingHistory","coin":"ETH","startTime":1,"endTime":2}, opener=opener)
    assert exc.value.status == status
    assert len(seen)==1


def test_timeout_and_response_size_are_bounded():
    seen=[]
    def timeout(request,timeout):
        seen.append(request)
        raise TimeoutError("fixture")
    with pytest.raises(ProviderError,match="TIMEOUT"):
        _request({"type":"fundingHistory","coin":"ETH","startTime":1,"endTime":2},opener=timeout)
    assert len(seen)==1
    with pytest.raises(ProviderError,match="RESPONSE_TOO_LARGE"):
        _request({"type":"fundingHistory","coin":"ETH","startTime":1,"endTime":2},
                 opener=lambda *args,**kwargs:FakeResponse(b" "*101),max_response_bytes=100)


@pytest.mark.parametrize("body",[b"bad",b"{}",b"NaN",b"[{\"x\":1e999}]"])
def test_malformed_responses_rejected(body):
    with pytest.raises(ProviderError):
        _request({"type":"fundingHistory","coin":"ETH","startTime":1,"endTime":2},
                 opener=lambda *args,**kwargs:FakeResponse(body))


def test_exact_instrument_window_and_response_integrity_are_enforced():
    now=int(time.time()*1000)
    start,end=now-60_000,now
    client=HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(json.dumps([{"coin":"BTC","time":end,"fundingRate":"0.1"}]).encode()))
    with pytest.raises(ProviderError,match="IDENTITY_MISMATCH"):
        client.funding_history("ETH",start,end)
    for args in [("bad/coin",start,end),("ETH",end,start),("ETH",start,now+3_600_000),
                 ("ETH",now-7*86_400_000-1,now)]:
        with pytest.raises(ValueError):
            client.funding_history(*args)

    seven_days = now - 7*86_400_000
    client = HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(b"[]"))
    assert client.funding_history("ETH",seven_days,now)["requested_window_ms"]["start"] == seven_days

    client=HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(json.dumps([{"coin":"ETH","time":end+1,"fundingRate":"0.1"}]).encode()))
    with pytest.raises(ProviderError,match="out-of-window"):
        client.funding_history("ETH",start,end)
    client=HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(json.dumps([{"coin":"ETH","time":end,"fundingRate":"NaN"}]).encode()))
    with pytest.raises(ProviderError,match="finite"):
        client.funding_history("ETH",start,end)


def test_funding_history_cache_is_bounded_and_uses_one_request_per_call():
    now=int(time.time()*1000)
    calls=[]
    payload=json.dumps([]).encode()
    client=HyperliquidClient(opener=lambda *args,**kwargs:(calls.append(args) or FakeResponse(payload)),clock=lambda:0)
    assert client.funding_history("ETH",now-3_600_000,now)["cache"]=="miss"
    assert client.funding_history("ETH",now-3_600_000,now)["cache"]=="hit"
    assert len(calls)==1


def test_funding_history_response_row_count_is_capped():
    now = int(time.time() * 1000)
    rows = [{"coin":"ETH", "time":now-1, "fundingRate":"0.0001"} for _ in range(module.MAX_FUNDING_ROWS+1)]
    client = HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(json.dumps(rows).encode()))
    with pytest.raises(ProviderError, match="bounded 200-row"):
        client.funding_history("ETH",now-60_000,now)


def test_perp_snapshot_maps_exact_coin_and_discloses_current_oi_scope():
    payload = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [{"openInterest": "100", "funding": "0.0001"},
         {"openInterest": "200.5", "funding": "-0.0002", "markPx": "2500", "oraclePx": "2499", "dayNtlVlm": "500000"}],
    ]
    seen=[]
    client=HyperliquidClient(opener=lambda request,timeout:(seen.append(request) or FakeResponse(json.dumps(payload).encode())),
                             utc_clock=lambda:"2026-10-17T16:00:00Z")
    result=client.perp_snapshot("ETH")
    assert json.loads(seen[0].data)=={"type":"metaAndAssetCtxs"}
    assert seen[0].method=="POST" and seen[0].get_header("Authorization") is None
    assert result["data"]["openInterest"]=={"raw":"200.5","numeric":200.5}
    assert result["observation_timestamp"] is None
    assert result["coverage_status"]=="CURRENT_SNAPSHOT_ONLY; not historical OI"


@pytest.mark.parametrize("payload", [
    {}, [{"universe": []}], [{"universe": [{"name":"ETH"}]}, []],
    [{"universe": [{"name":"ETH"},{"name":"ETH"}]}, [{},{}]],
    [{"universe": [{"name":"ETH"}]}, [{"openInterest":"NaN"}]],
])
def test_perp_snapshot_rejects_malformed_or_unverifiable_payloads(payload):
    client=HyperliquidClient(opener=lambda *args,**kwargs:FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        client.perp_snapshot("ETH")


def test_only_the_two_explicit_info_request_shapes_are_allowed():
    for body in [{"type":"allMids"}, {"type":"fundingHistory","coin":"ETH"}, {"type":"metaAndAssetCtxs","dex":""}]:
        with pytest.raises(ValueError):
            _request(body,opener=lambda *args,**kwargs:pytest.fail("network should not be called"))


def test_retained_live_smokes_bind_exact_eth_scope_and_runtime_readiness():
    funding=json.loads((ROOT/"research-runs/provider-smokes/hyperliquid-eth-funding-24h.json").read_text(encoding="utf-8"))
    snapshot=json.loads((ROOT/"research-runs/provider-smokes/hyperliquid-eth-perp-snapshot.json").read_text(encoding="utf-8"))
    route=json.loads((ROOT/".codex/hyperliquid-routing.json").read_text(encoding="utf-8"))
    expanded=json.loads((ROOT/"research-runs/provider-smokes/hyperliquid-eth-funding-7d-2026-10-17.json").read_text(encoding="utf-8"))
    replay=json.loads((ROOT/"research-runs/2026-10-17-hyperliquid-eth-funding-7d-replay/manifest.json").read_text(encoding="utf-8"))
    replay_observations=json.loads((ROOT/"research-runs/2026-10-17-hyperliquid-eth-funding-7d-replay/observations.json").read_text(encoding="utf-8"))
    assert funding["smoke_status"]==snapshot["smoke_status"]=="RESPONDED"
    assert funding["authorization_header_sent"] is False and snapshot["authorization_header_sent"] is False
    assert funding["result"]["requested_coin"]=="ETH"
    assert funding["result"]["row_count"]==24
    assert snapshot["result"]["requested_coin"]=="ETH"
    assert snapshot["result"]["observation_timestamp"] is None
    assert snapshot["result"]["coverage_status"].startswith("CURRENT_SNAPSHOT_ONLY")
    assert route["access"]["runtime_readiness"].startswith("RESPONDED_FOR_ETH")
    assert expanded["smoke_status"] == "RESPONDED"
    assert expanded["requested_coin"] == "ETH"
    assert expanded["response_summary"]["row_count"] == 168
    assert expanded["authorization_header_sent"] is False
    assert expanded["raw_response_retained"] is False
    assert route["supported_routes"][0]["runtime_evidence"]["expanded_window_smoke"]["window_hours"] == 168
    assert replay["calculations"][0]["status"] == "REPLAYABLE"
    assert replay["calculations"][0]["formula"] == "sampled_extrema.v1"
    assert len(replay_observations["observations"]) == 168
    assert all(row["provider_rate_raw"] is not None and row["provider_time_ms"] for row in replay_observations["observations"])
