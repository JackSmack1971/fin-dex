import datetime as dt
import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/defillama_public.py"
SPEC = importlib.util.spec_from_file_location("defillama_public", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
BASE_URL = module.BASE_URL
STABLECOIN_BASE_URL = module.STABLECOIN_BASE_URL
DefiLlamaClient = module.DefiLlamaClient
ProviderError = module.ProviderError
_request = module._request
freshness_status = module.freshness_status
sources_are_independent = module.sources_are_independent


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


def test_protocol_history_uses_fixed_host_and_preserves_retrieval_metadata():
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return FakeResponse(b'{"name":"Aave","chains":["Ethereum"],"chainTvls":[]}')

    client = DefiLlamaClient(opener=opener, utc_clock=lambda: "2026-10-17T12:00:00Z")
    result = client.protocol_history("aave")
    assert seen[0][0].full_url == BASE_URL + "/protocol/aave"
    assert seen[0][0].method == "GET"
    assert result["provider"] == "defillama"
    assert result["upstream_origin"] == "defillama_aggregated_index"
    assert result["retrieved_at_utc"] == "2026-10-17T12:00:00Z"
    assert result["data"]["chains"] == ["Ethereum"]


def test_stablecoin_and_historical_routes_use_expected_read_only_urls():
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(b"[]")

    client = DefiLlamaClient(opener=opener)
    client.stablecoins()
    client.stablecoin_history(chain="Ethereum", stablecoin_id=1)
    client.historical_chain_tvl(chain="Ethereum")
    assert seen[0] == STABLECOIN_BASE_URL + "/stablecoins?includePrices=false"
    assert seen[1] == STABLECOIN_BASE_URL + "/stablecoincharts/Ethereum?stablecoin=1"
    assert seen[2] == BASE_URL + "/v2/historicalChainTvl/Ethereum"


def test_current_protocol_tvl_accepts_numeric_fixture():
    client = DefiLlamaClient(opener=lambda request, timeout: FakeResponse(b"123456.78"))
    result = client.protocol_tvl("aave")
    assert result["endpoint"] == "GET /tvl/{protocol}"
    assert result["data"] == 123456.78


def test_fees_route_restricts_data_type_and_uses_bounded_chart_flags():
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(b'{"protocols":[],"totalDataChart":[]}')

    client = DefiLlamaClient(opener=opener)
    client.fees(data_type="dailyRevenue", chain="Ethereum")
    assert seen[0].startswith(BASE_URL + "/overview/fees/Ethereum?")
    assert "dataType=dailyRevenue" in seen[0]
    assert "excludeTotalDataChart=true" in seen[0]
    with pytest.raises(ValueError):
        client.fees(data_type="arbitrary")


def test_protocol_fee_summary_uses_fixed_protocol_route_and_data_type():
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(b'{"slug":"aave-v3","totalDataChart":[[1,2]]}')

    result = DefiLlamaClient(opener=opener).protocol_fees("aave-v3", data_type="dailyRevenue")
    assert seen == [BASE_URL + "/summary/fees/aave-v3?dataType=dailyRevenue"]
    assert result["endpoint"] == "GET /summary/fees/{protocol}"
    assert result["data"]["totalDataChart"] == [[1, 2]]
    with pytest.raises(ValueError):
        DefiLlamaClient(opener=opener).protocol_fees("../other")
    with pytest.raises(ValueError):
        DefiLlamaClient(opener=opener).protocol_fees("aave-v3", data_type="arbitrary")


def test_open_interest_overview_keeps_dated_chart_and_excludes_breakdown():
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(b'{"totalDataChart":[[1,2]],"protocols":[]}')

    result = DefiLlamaClient(opener=opener).open_interest()
    assert seen == [BASE_URL + "/overview/open-interest?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"]
    assert result["endpoint"] == "GET /overview/open-interest"
    assert result["data"]["totalDataChart"] == [[1, 2]]


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (402, "PAYMENT_REQUIRED"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED")
])
def test_auth_payment_and_rate_limit_failures_stop_provider_branch(code, status):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as error:
        _request("/protocols", opener=opener)
    assert error.value.status == status


def test_timeout_is_distinct_and_not_retried():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture timeout")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request("/protocols", opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not json", b'"scalar"', b"null", b"true", b"{\"x\":1e999}"])
def test_malformed_or_unsupported_json_is_rejected(body):
    with pytest.raises(ProviderError) as error:
        _request("/protocols", opener=lambda request, timeout: FakeResponse(body))
    assert error.value.status == "MALFORMED_DATA"


def test_response_size_is_bounded():
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request("/protocols", opener=lambda request, timeout: FakeResponse(b" " * 100), max_response_bytes=10)


def test_cache_is_process_local_and_expires():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(b"[]")

    tick = [0.0]
    client = DefiLlamaClient(opener=opener, clock=lambda: tick[0], cache_ttl_seconds=10)
    assert client.protocols()["cache"] == "miss"
    assert client.protocols()["cache"] == "hit"
    tick[0] = 11.0
    assert client.protocols()["cache"] == "miss"
    assert len(calls) == 2


def test_zero_cache_ttl_disables_cache():
    calls = []
    client = DefiLlamaClient(
        opener=lambda request, timeout: (calls.append(request) or FakeResponse(b"[]")),
        cache_ttl_seconds=0,
    )
    client.protocols()
    client.protocols()
    assert len(calls) == 2


def test_bad_chain_or_protocol_identity_is_rejected_before_network():
    calls = []
    client = DefiLlamaClient(opener=lambda *args, **kwargs: calls.append(args))
    for value in ("", "../Ethereum", "Ethereum/../../x", "https://other.example"):
        with pytest.raises(ValueError):
            client.historical_chain_tvl(chain=value)
    with pytest.raises(ValueError):
        client.protocol_history("../../secret")
    with pytest.raises(ValueError):
        client.stablecoin_history(stablecoin_id=0)
    assert calls == []


def test_only_documented_fixed_paths_are_callable():
    for path in ("/api/v1/token_security/1", "/usage/secret", "//other.example/protocols", "/pro/overview/fees"):
        with pytest.raises(ValueError):
            _request(path, opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        _request("/stablecoins", base_url="https://attacker.invalid", opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        _request("/stablecoinchains", base_url=BASE_URL, opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        _request("/protocols", base_url=STABLECOIN_BASE_URL, opener=lambda *args, **kwargs: pytest.fail("network should not be called"))


def test_historical_observation_freshness_is_explicit_and_timezone_aware():
    now = dt.datetime(2026, 10, 17, 12, tzinfo=dt.timezone.utc)
    assert freshness_status("2026-10-17T11:59:00Z", now_utc=now, max_age_seconds=120) == "FRESH"
    assert freshness_status("2026-10-17T11:00:00Z", now_utc=now, max_age_seconds=120) == "STALE"
    assert freshness_status("2026-10-16", now_utc=now, max_age_seconds=120) == "UNVERIFIED"
    assert freshness_status("2026-10-17T12:01:00Z", now_utc=now, max_age_seconds=120) == "UNVERIFIED"
    assert freshness_status(None, now_utc=now, max_age_seconds=120) == "UNVERIFIED"


def test_wrappers_over_same_upstream_do_not_count_as_independent():
    assert not sources_are_independent(
        {"provider": "wrapper_a", "upstream_origin": "defillama_aggregated_index"},
        {"provider": "wrapper_b", "upstream_origin": "defillama_aggregated_index"},
    )
    assert not sources_are_independent({"provider": "x"}, {"provider": "y"})
    assert sources_are_independent(
        {"upstream_origin": "defillama_aggregated_index"},
        {"upstream_origin": "chain_rpc_observation"},
    )


def test_no_fallback_is_explicitly_reported_as_exhausted():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    assert all(route["fallback"] == [] for route in policy["routing"])
    assert "No fallback is currently qualified" in policy["request_policy"]["fallback_policy"]


def test_cli_escapes_provider_unicode_for_legacy_console_encodings(monkeypatch, capsys):
    class FakeClient:
        def fees(self, *, data_type, chain):
            return {"provider": "defillama", "data_type": data_type, "chain": chain, "label": "A → B"}

    monkeypatch.setattr(module, "DefiLlamaClient", FakeClient)
    assert module.main(["fees", "--data-type", "dailyRevenue", "--chain", "Ethereum"]) == 0
    output = capsys.readouterr().out.rstrip("\n")
    assert all(ord(char) < 128 for char in output)
    assert json.loads(output)["label"] == "A → B"


def test_saved_tvl_smoke_is_endpoint_specific():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    artifact = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8-sig"))
    assert policy["access"]["runtime_readiness"] == "PARTIALLY_QUALIFIED"
    assert policy["documentation"] == "https://api-docs.defillama.com/llms-free.txt"
    assert policy["access"]["auth"] == "none"
    assert policy["access"]["registration_is_authorization"] is False
    assert "without a payment or credential challenge" in policy["access"]["smoke_evidence"]["qualification"]
    assert artifact["endpoint"] == "GET /tvl/{protocol}"
    assert artifact["retrieved_at_utc"] == "2026-10-17T13:50:47Z"
    assert isinstance(artifact["data"], (int, float)) and artifact["data"] > 0
    prior_wrong_host = next(x for x in policy["access"]["endpoint_observations"]
                            if x["endpoint"] == "GET /stablecoinchains" and x["status"] == "MISROUTED_PRIOR_ATTEMPT")
    assert prior_wrong_host["retrieved_at_utc"] is None
    assert "not a valid endpoint availability observation" in prior_wrong_host["record_quality"]


def test_saved_historical_chain_tvl_smoke_and_replay_bind_exact_ethereum_query():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"] if x["endpoint"] == "GET /v2/historicalChainTvl/{chain}")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8"))
    assert evidence["status"] == "QUALIFIED_FOR_ETHEREUM_RESPONSE_SHAPE"
    assert smoke["source_url"] == evidence["source_url"]
    assert smoke["retrieved_at_utc"] == evidence["retrieved_at_utc"]
    assert smoke["source_url"].endswith("/v2/historicalChainTvl/Ethereum")
    rows = smoke["data"]
    assert len(rows) == 3308
    assert all(isinstance(row["date"], int) and isinstance(row["tvl"], (int, float)) for row in rows)
    assert all(left["date"] < right["date"] for left, right in zip(rows, rows[1:]))


def test_saved_ethereum_daily_revenue_smoke_preserves_unit_and_time_gaps():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"]
                   if x["endpoint"] == "GET /overview/fees[/{chain}]")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8"))
    assert evidence["status"] == "QUALIFIED_FOR_ETHEREUM_DAILY_REVENUE_RESPONSE_SHAPE"
    assert smoke["source_url"] == evidence["source_url"]
    assert smoke["retrieved_at_utc"] == evidence["retrieved_at_utc"] == "2026-10-17T14:12:36Z"
    assert "dataType=dailyRevenue" in smoke["source_url"]
    assert len(smoke["data"]["protocols"]) == 605
    assert smoke["data"]["totalDataChart"] == []
    aave = next(x for x in smoke["data"]["protocols"] if x.get("slug") == "aave-v3")
    assert (aave["total24h"], aave["total7d"], aave["total30d"]) == (112525, 951462, 3910731)
    assert evidence["unit"].startswith("UNVERIFIED")
    assert evidence["freshness"].startswith("UNVERIFIED")


def test_saved_protocol_daily_revenue_history_preserves_dates_and_unit_gap():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"]
                   if x["endpoint"] == "GET /summary/fees/{protocol}")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8-sig"))
    assert evidence["status"] == "QUALIFIED_FOR_AAVE_V3_DAILY_REVENUE_HISTORICAL_RESPONSE_SHAPE"
    assert smoke["http_status"] == 200
    assert smoke["source_url"] == evidence["source_url"]
    assert smoke["protocol_slug"] == "aave-v3"
    assert smoke["data_type"] == "dailyRevenue"
    chart = smoke["totalDataChart"]
    assert len(chart) == smoke["response_chart_rows"] > 1000
    assert all(isinstance(row, list) and len(row) == 2 and isinstance(row[0], int) for row in chart)
    assert all(left[0] < right[0] for left, right in zip(chart, chart[1:]))
    assert chart[0] == smoke["chart_first"] and chart[-1] == smoke["chart_last"]
    assert evidence["unit"].startswith("UNVERIFIED")
    assert "exact daily timestamp semantics remain unverified" in evidence["freshness"] or evidence["source_timestamp"].startswith("Unix-second date")


def test_saved_open_interest_smoke_is_aggregate_context_only():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"]
                   if x["endpoint"] == "GET /overview/open-interest")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8-sig"))
    assert evidence["status"] == "QUALIFIED_FOR_PROVIDER_AGGREGATE_HISTORICAL_RESPONSE_SHAPE"
    assert smoke["http_status"] == 200
    assert smoke["source_url"] == evidence["source_url"]
    assert smoke["protocol_count"] == 142
    chart = smoke["totalDataChart"]
    assert len(chart) == smoke["chart_rows"] == 2061
    assert all(isinstance(row, list) and len(row) == 2 and isinstance(row[0], int) for row in chart)
    assert all(left[0] < right[0] for left, right in zip(chart, chart[1:]))
    assert chart[0] == smoke["chart_first"] and chart[-1] == smoke["chart_last"]
    assert evidence["unit"].startswith("UNVERIFIED")
    assert "not exchange-specific" in evidence["identity"]


def test_saved_stablecoin_chain_smoke_binds_the_stablecoins_api_host():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"]
                   if x.get("evidence_artifact") == "research-runs/provider-smokes/defillama-stablecoin-chains.json")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8"))
    assert evidence["status"] == "QUALIFIED_FOR_STABLECOIN_CHAIN_RESPONSE_SHAPE"
    assert smoke["source_url"] == STABLECOIN_BASE_URL + "/stablecoinchains"
    assert smoke["retrieved_at_utc"] == evidence["retrieved_at_utc"] == "2026-10-17T14:14:42Z"
    rows = smoke["data"]
    assert len(rows) == 209
    assert all(isinstance(row.get("name"), str) and row["name"] for row in rows)
    assert all(isinstance(row.get("totalCirculatingUSD"), dict) for row in rows)
    assert evidence["source_timestamp"] == "not supplied"
    assert evidence["interpretation"] == "circulating market-cap proxy; not transfer-level stablecoin flow"


def test_saved_ethereum_stablecoin_chart_preserves_dates_units_and_supply_not_flow_semantics():
    policy = json.loads((ROOT / ".codex/data-provider-routing.json").read_text(encoding="utf-8"))
    evidence = next(x for x in policy["access"]["endpoint_observations"]
                   if x["endpoint"] == "GET /stablecoincharts/{chain|all}")
    smoke = json.loads((ROOT / evidence["evidence_artifact"]).read_text(encoding="utf-8"))
    assert evidence["status"] == "QUALIFIED_FOR_ETHEREUM_HISTORICAL_RESPONSE_SHAPE"
    assert smoke["source_url"] == "https://stablecoins.llama.fi/stablecoincharts/Ethereum"
    assert smoke["retrieved_at_utc"] == evidence["retrieved_at_utc"] == "2026-10-17T14:23:04Z"
    rows = smoke["data"]
    assert len(rows) == 3245
    dates = [int(row["date"]) for row in rows]
    assert all(right - left == 86400 for left, right in zip(dates, dates[1:]))
    assert rows[-1]["totalCirculatingUSD"]["peggedUSD"] == 144946205700.82
    assert evidence["unit"].startswith("USD")
    assert evidence["interpretation"] == "historical circulating market-cap/supply context, not transfer-level net flows"
