import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/goplus_token_security.py"
SPEC = importlib.util.spec_from_file_location("goplus_token_security", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASE_URL = module.BASE_URL
GoPlusClient = module.GoPlusClient
ProviderError = module.ProviderError
_request = module._request
CONTRACT = "0x1111111111111111111111111111111111111111"
SOLANA_MINT = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"


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


def response_for(record=None, address=CONTRACT, code=1, message="ok"):
    result = {} if record is None else {address.lower(): record}
    return {"code": code, "message": message, "result": result}


def test_fixed_anonymous_get_exact_chain_contract_and_raw_risk_fields():
    seen = []
    payload = response_for({"token_name": "Fixture", "is_honeypot": "0", "owner_change_balance": "1"})

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(json.dumps(payload).encode())

    client = GoPlusClient(opener=opener, utc_clock=lambda: "2026-10-17T14:10:00Z")
    result = client.token_security("1", CONTRACT)
    assert seen[0].full_url == f"{BASE_URL}/api/v1/token_security/1?contract_addresses={CONTRACT}"
    assert seen[0].method == "GET"
    assert "Authorization" not in seen[0].headers
    assert result["chain_id"] == "1"
    assert result["contract_address"] == CONTRACT
    assert result["security_fields"]["is_honeypot"] == "0"
    assert result["security_fields"]["owner_change_balance"] == "1"
    assert result["observation_timestamp"] is None
    assert result["freshness_status"] == "UNVERIFIED_NO_PROVIDER_TIMESTAMP"
    assert result["interpretation"].startswith("Automated heuristic screening only")


def test_solana_route_is_anonymous_exact_mint_and_case_sensitive():
    seen = []
    payload = {"code": 1, "message": "ok", "result": {SOLANA_MINT: {"mintable": {"status": "0"}}}}

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(json.dumps(payload).encode())

    result = GoPlusClient(opener=opener).token_security("solana", SOLANA_MINT)
    assert seen[0].full_url == f"{BASE_URL}/api/v1/solana/token_security?contract_addresses={SOLANA_MINT}"
    assert seen[0].method == "GET"
    assert "Authorization" not in seen[0].headers
    assert result["chain_id"] == "solana"
    assert result["contract_address"] == SOLANA_MINT
    assert result["endpoint"] == "GET /api/v1/solana/token_security"
    assert result["security_fields"] == {"mintable": {"status": "0"}}


def test_solana_address_case_mismatch_is_an_identity_failure():
    payload = {"code": 1, "message": "ok", "result": {SOLANA_MINT.lower(): {"mintable": {"status": "0"}}}}
    client = GoPlusClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        client.token_security("solana", SOLANA_MINT)


@pytest.mark.parametrize(("chain", "address"), [
    ("", CONTRACT), ("ethereum", CONTRACT), ("../1", CONTRACT), ("1", ""),
    ("1", "0x1234"), ("1", "0x" + "g" * 40), ("56", "0x" + "1" * 40 + "/../../x"),
    ("solana", "0OIl-not-base58"), ("solana", "1" * 33)
])
def test_invalid_chain_or_contract_rejected_before_network(chain, address):
    client = GoPlusClient(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.token_security(chain, address)


@pytest.mark.parametrize(("code", "status"), [
    (2, "PARTIAL_DATA"), (2004, "INVALID_CONTRACT_ADDRESS"), (2018, "UNSUPPORTED_CHAIN"),
    (2020, "NOT_A_CONTRACT"), (2021, "NO_SECURITY_DATA"), (2022, "UNSUPPORTED_CHAIN"),
    (4023, "AUTH_REQUIRED"), (4029, "RATE_LIMITED"), (9999, "PROVIDER_ERROR")
])
def test_api_status_codes_are_preserved_as_distinct_failures(code, status):
    payload = {"code": code, "message": "fixture", "result": {}}
    client = GoPlusClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError) as error:
        client.token_security("1", CONTRACT)
    assert error.value.status == status


def test_no_info_result_is_not_zero_or_security_pass():
    payload = response_for()
    client = GoPlusClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    result = client.token_security("1", CONTRACT)
    assert result["response_status"] == "NO_SECURITY_DATA"
    assert result["security_fields"] is None
    assert result["freshness_status"] == "NO_SECURITY_DATA"
    assert result["missing_fields_are_unknown"] is True


@pytest.mark.parametrize("payload", [
    [], {"message": "missing code", "result": {}},
    {"code": 1, "message": "ok", "result": []},
    {"code": 1, "message": "ok", "result": {"0x2222222222222222222222222222222222222222": {"is_honeypot": "0"}}},
])
def test_malformed_or_wrong_contract_payload_rejected(payload):
    client = GoPlusClient(opener=lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        client.token_security("1", CONTRACT)


@pytest.mark.parametrize(("http_code", "status"), [
    (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (402, "PAYMENT_REQUIRED"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED")
])
def test_http_auth_payment_and_rate_failures_stop_without_retry(http_code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, http_code, "fixture", {}, None)

    with pytest.raises(ProviderError) as error:
        _request("1", CONTRACT, opener=opener)
    assert error.value.status == status
    assert len(calls) == 1


def test_timeout_and_malformed_json_are_classified():
    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request("1", CONTRACT, opener=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(ProviderError, match="MALFORMED_DATA"):
        _request("1", CONTRACT, opener=lambda request, timeout: FakeResponse(b"not json"))


def test_response_size_is_bounded():
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request("1", CONTRACT, max_response_bytes=5,
                 opener=lambda request, timeout: FakeResponse(b"123456"))


def test_cache_is_process_local_and_expires():
    calls = []
    payload = response_for({"is_honeypot": "0"})

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(json.dumps(payload).encode())

    tick = [0.0]
    client = GoPlusClient(opener=opener, clock=lambda: tick[0], cache_ttl_seconds=5)
    assert client.token_security("1", CONTRACT)["cache"] == "miss"
    assert client.token_security("1", CONTRACT)["cache"] == "hit"
    tick[0] = 6
    assert client.token_security("1", CONTRACT)["cache"] == "miss"
    assert len(calls) == 2


def test_saved_base_usdc_smoke_binds_exact_chain_contract_and_unverified_freshness():
    artifact = json.loads((ROOT / "research-runs/provider-smokes/goplus-base-usdc.json").read_text(encoding="utf-8-sig"))
    assert artifact["chain_id"] == "8453"
    assert artifact["contract_address"].lower() == "0x833589fc d6edb6e08f4c7c32d4f71b54bda02913".replace(" ", "")
    assert artifact["source_url"].endswith("/token_security/8453?contract_addresses=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
    assert artifact["response_status"] == "RESPONDED"
    assert artifact["security_fields"]["token_name"] == "USD Coin"
    assert artifact["security_fields"]["token_symbol"] == "USDC"
    assert artifact["observation_timestamp"] is None
    assert artifact["freshness_status"] == "UNVERIFIED_NO_PROVIDER_TIMESTAMP"
    assert artifact["missing_fields_are_unknown"] is True


def test_saved_solana_pyth_smoke_binds_exact_mint_and_unverified_freshness():
    artifact = json.loads((ROOT / "research-runs/provider-smokes/goplus-solana-pyth.json").read_text(encoding="utf-8-sig"))
    assert artifact["chain_id"] == "solana"
    assert artifact["contract_address"] == SOLANA_MINT
    assert artifact["source_url"].endswith(f"/solana/token_security?contract_addresses={SOLANA_MINT}")
    assert artifact["response_status"] == "RESPONDED"
    assert artifact["security_fields"]["metadata"]["name"] == "Pyth Network"
    assert artifact["security_fields"]["metadata"]["symbol"] == "PYTH"
    assert artifact["observation_timestamp"] is None
    assert artifact["freshness_status"] == "UNVERIFIED_NO_PROVIDER_TIMESTAMP"
    assert artifact["missing_fields_are_unknown"] is True
