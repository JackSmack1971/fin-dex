import importlib.util
import json
import pathlib
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/ankr_public_ethereum.py"
SPEC = importlib.util.spec_from_file_location("ankr_public_ethereum", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

AnkrEthereumPublicClient = module.AnkrEthereumPublicClient
ProviderError = module.ProviderError
TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
TRANSFER = module.TRANSFER_TOPIC
HASH = "0x" + "a" * 64


@pytest.fixture(autouse=True)
def qualified_fixture_route(tmp_path, monkeypatch):
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"access": {"runtime_readiness": "READY_ZERO_CHARGE_REQUALIFIED"}}), encoding="utf-8")
    monkeypatch.setattr(module, "ROUTE_CONTRACT", route)


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload, url=module.ENDPOINT):
        self.raw = json.dumps(payload).encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.raw[:limit]

    def geturl(self):
        return self.url


def _log(**overrides):
    row = {
        "address": TOKEN,
        "topics": [TRANSFER, "0x" + "0" * 24 + "1" * 40, "0x" + "0" * 24 + "2" * 40],
        "data": "0x" + "0" * 63 + "5",
        "blockNumber": "0x10",
        "blockHash": HASH,
        "transactionHash": HASH,
        "logIndex": "0x0",
        "removed": False,
    }
    row.update(overrides)
    return row


def _rpc_response(method, result, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def test_transfer_logs_validate_chain_and_preserve_raw_units_without_inferred_time():
    requests = []

    def opener(request, timeout):
        body = json.loads(request.data)
        requests.append((request, body, timeout))
        result = "0x1" if body["method"] == "eth_chainId" else [_log()]
        return Response(_rpc_response(body["method"], result))

    result = AnkrEthereumPublicClient(opener=opener, utc_clock=lambda: "2026-10-17T16:00:00Z").transfer_logs(TOKEN, 16, 20)
    assert len(requests) == 2
    assert all(request.full_url == module.ENDPOINT and request.get_method() == "POST" for request, _, _ in requests)
    assert all("Authorization" not in request.headers for request, _, _ in requests)
    assert requests[1][1]["method"] == "eth_getLogs"
    assert requests[1][1]["params"] == [{
        "address": TOKEN,
        "fromBlock": "0x10",
        "toBlock": "0x14",
        "topics": [TRANSFER],
    }]
    assert result["status"] == "RESPONDED"
    assert result["retrieved_at_utc"] == "2026-10-17T16:00:00Z"
    assert result["data"][0]["amount_raw_integer"] == "5"
    assert result["data"][0]["block_timestamp_utc"] is None
    assert result["coverage_status"].startswith("BOUNDED_RPC_RESPONSE")


@pytest.mark.parametrize("token,start,end", [
    ("bad", 1, 1), (TOKEN, -1, 1), (TOKEN, 2, 1), (TOKEN, 0, 1000),
])
def test_invalid_scope_fails_before_network(token, start, end):
    client = AnkrEthereumPublicClient(opener=lambda *args, **kwargs: pytest.fail("network must not be called"))
    with pytest.raises(ValueError):
        client.transfer_logs(token, start, end)


def test_chain_mismatch_stops_before_logs_request():
    calls = []

    def opener(request, timeout):
        calls.append(json.loads(request.data)["method"])
        return Response(_rpc_response("eth_chainId", "0x89"))

    with pytest.raises(ProviderError) as error:
        AnkrEthereumPublicClient(opener=opener).transfer_logs(TOKEN, 1, 1)
    assert error.value.status == "IDENTITY_MISMATCH"
    assert calls == ["eth_chainId"]


@pytest.mark.parametrize("bad_log,status", [
    (_log(address="0x0000000000000000000000000000000000000001"), "IDENTITY_MISMATCH"),
    (_log(topics=[TRANSFER, "bad", "bad"]), "MALFORMED_DATA"),
    (_log(topics=[TRANSFER, "0x" + "1" * 64, "0x" + "0" * 24 + "2" * 40]), "MALFORMED_DATA"),
    (_log(blockNumber="0x15"), "IDENTITY_MISMATCH"),
    (_log(removed="false"), "MALFORMED_DATA"),
])
def test_invalid_log_rows_are_rejected(bad_log, status):
    responses = iter([Response(_rpc_response("eth_chainId", "0x1")), Response(_rpc_response("eth_getLogs", [bad_log]))])
    client = AnkrEthereumPublicClient(opener=lambda *args, **kwargs: next(responses))
    with pytest.raises(ProviderError) as error:
        client.transfer_logs(TOKEN, 16, 20)
    assert error.value.status == status


@pytest.mark.parametrize("payload,status", [
    ({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "method unavailable"}}, "RPC_ERROR"),
    ({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Unauthorized: API key required"}}, "UNAUTHORIZED"),
    ({"jsonrpc": "2.0", "id": 9, "result": "0x1"}, "MALFORMED_DATA"),
    ({"jsonrpc": "2.0", "id": 1, "result": "0x1", "extra": float("nan")}, "MALFORMED_DATA"),
])
def test_rpc_errors_and_malformed_envelopes_stop(payload, status):
    client = AnkrEthereumPublicClient(opener=lambda *args, **kwargs: Response(payload))
    with pytest.raises(ProviderError) as error:
        client.transfer_logs(TOKEN, 1, 1)
    assert error.value.status == status


def test_http_payment_denial_is_classified_without_retry():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 402, "payment required", {}, None)

    with pytest.raises(ProviderError) as error:
        AnkrEthereumPublicClient(opener=opener).transfer_logs(TOKEN, 1, 1)
    assert error.value.status == "PAYMENT_REQUIRED"
    assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [
    (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (429, "RATE_LIMITED"),
])
def test_auth_forbidden_and_rate_limit_stop_without_retry(status, expected):
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, status, "provider denied", {}, None)

    with pytest.raises(ProviderError) as error:
        AnkrEthereumPublicClient(opener=opener).transfer_logs(TOKEN, 1, 1)
    assert error.value.status == expected
    assert len(calls) == 1


def test_timeout_stops_without_retry():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise TimeoutError("fixture timeout")

    with pytest.raises(ProviderError) as error:
        AnkrEthereumPublicClient(opener=opener).transfer_logs(TOKEN, 1, 1)
    assert error.value.status == "TIMEOUT"
    assert len(calls) == 1


def test_redirect_off_fixed_host_is_rejected():
    client = AnkrEthereumPublicClient(opener=lambda *args, **kwargs: Response(_rpc_response("eth_chainId", "0x1"), "https://example.com/eth"))
    with pytest.raises(ProviderError) as error:
        client.transfer_logs(TOKEN, 1, 1)
    assert error.value.status == "UNSAFE_REDIRECT"


def test_routing_contract_limits_methods_cost_and_terms():
    contract = json.loads((ROOT / ".codex/ankr-public-ethereum-routing.json").read_text(encoding="utf-8"))
    assert contract["route"]["allowed_methods"] == ["eth_chainId", "eth_getLogs"]
    assert contract["route"]["block_range"].startswith("Inclusive, at most 1,000 blocks")
    assert contract["route"]["requests_per_invocation"] == 2
    assert contract["access"]["runtime_readiness"] == "UNAVAILABLE_ANONYMOUS_ACCESS_REQUIRES_KEY"
    assert contract["access"]["smoke_evidence"] == "research-runs/provider-smokes/ankr-ethereum-public-eth-blocknumber-401.json"
    assert "internal research only" in contract["access"]["terms_scope"]


def test_current_route_contract_denial_blocks_before_any_network_request(tmp_path):
    route = tmp_path / "disabled.json"
    route.write_text(json.dumps({"access": {"runtime_readiness": "UNAVAILABLE_ANONYMOUS_ACCESS_REQUIRES_KEY"}}), encoding="utf-8")
    client = AnkrEthereumPublicClient(
        opener=lambda *args, **kwargs: pytest.fail("disabled route must not make a request"),
        route_contract=route,
    )
    with pytest.raises(ProviderError) as error:
        client.transfer_logs(TOKEN, 1, 1)
    assert error.value.status == "ROUTE_UNAVAILABLE"

\n