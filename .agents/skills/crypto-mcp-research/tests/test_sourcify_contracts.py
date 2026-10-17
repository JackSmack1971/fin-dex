from __future__ import annotations

import datetime as dt
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / ".agents/skills/crypto-mcp-research/scripts"))
import sourcify_contracts as sourcify


CHAIN = "8453"
ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
FIXTURE = ROOT / ".agents/skills/crypto-mcp-research/tests/fixtures/sourcify-base-usdc.json"


class Response:
    def __init__(self, body: bytes, *, status=200, content_type="application/json; charset=utf-8", url=None):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.url = url or f"https://sourcify.dev/server/v2/contract/{CHAIN}/{ADDRESS}"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self.body[:amount] if amount >= 0 else self.body

    def geturl(self):
        return self.url


def fixture_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def opener_for(payload, *, status=200, content_type="application/json; charset=utf-8", url=None, calls=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def opener(request, timeout):
        if calls is not None:
            calls.append(request)
        assert request.full_url.startswith("https://sourcify.dev/server/v2/contract/")
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") is None
        assert "fin-dex-read-only-research/1.0" in request.get_header("User-agent")
        return Response(body, status=status, content_type=content_type, url=url)

    return opener


def test_exact_contract_lookup_returns_minimal_identity_matched_metadata(tmp_path):
    result = sourcify.SourcifyClient(opener=opener_for(fixture_payload()), cache_dir=tmp_path).contract(CHAIN, ADDRESS)
    assert result["provider"] == "sourcify_contract_repository"
    assert result["chain_id"] == CHAIN
    assert result["address"].lower() == ADDRESS.lower()
    assert result["match"] == "exact_match"
    assert result["creation_match"] == "exact_match"
    assert result["runtime_match"] == "exact_match"
    assert result["provider_verified_at_utc"] == "2024-08-08T13:59:29Z"
    assert result["cache"] == "stored"
    assert "not a security audit" in result["interpretation"]
    assert "sources" not in result["data"]
    assert "stdJsonInput" not in result["data"]


def test_persisted_positive_cache_prevents_a_second_network_fetch(tmp_path):
    calls = []
    client = sourcify.SourcifyClient(opener=opener_for(fixture_payload(), calls=calls), cache_dir=tmp_path)
    first = client.contract(CHAIN, ADDRESS)
    second = sourcify.SourcifyClient(opener=lambda *a, **k: pytest.fail("positive data must be reused"), cache_dir=tmp_path).contract(CHAIN, ADDRESS)
    assert len(calls) == 1
    assert first["cache"] == "stored"
    assert second["cache"] == "persistent_hit"
    assert second["retrieved_at_utc"] == first["retrieved_at_utc"]
    assert second["data"] == first["data"]


def test_negative_response_is_persisted_and_reused_for_one_hour(tmp_path):
    payload = fixture_payload()
    payload.update(match=None, creationMatch=None, runtimeMatch=None, matchId=None, verifiedAt=None)
    now = [1000.0]
    calls = []
    def utc_clock():
        return dt.datetime.fromtimestamp(now[0], dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    client = sourcify.SourcifyClient(opener=opener_for(payload, calls=calls), cache_dir=tmp_path, clock=lambda: now[0], utc_clock=utc_clock)
    client.contract(CHAIN, ADDRESS)
    second = sourcify.SourcifyClient(opener=lambda *a, **k: pytest.fail("negative cache should still be fresh"), cache_dir=tmp_path, clock=lambda: now[0] + 3599).contract(CHAIN, ADDRESS)
    assert len(calls) == 1
    assert second["match"] is None
    assert second["cache"] == "persistent_hit"


@pytest.mark.parametrize(("status", "expected"), [
    (400, "INVALID_REQUEST"), (401, "UNAUTHORIZED"), (402, "PAYMENT_REQUIRED"),
    (403, "FORBIDDEN"), (404, "NOT_FOUND"), (429, "RATE_LIMITED"), (500, "HTTP_ERROR"),
])
def test_http_errors_stop_without_retry(status, expected, tmp_path):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "failure", {}, io.BytesIO(b""))
    client = sourcify.SourcifyClient(opener=opener, cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError) as error:
        client.contract(CHAIN, ADDRESS)
    assert error.value.status == expected


def test_timeout_maps_and_does_not_retry(tmp_path):
    client = sourcify.SourcifyClient(opener=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="TIMEOUT"):
        client.contract(CHAIN, ADDRESS)


@pytest.mark.parametrize("bad_chain,bad_address", [
    ("01", ADDRESS), ("0", ADDRESS), ("1/..", ADDRESS), (CHAIN, "0x1234"), (CHAIN, ADDRESS.lower().replace("0x", "0X")),
])
def test_invalid_identity_fails_before_network(bad_chain, bad_address, tmp_path):
    client = sourcify.SourcifyClient(opener=lambda *a, **k: pytest.fail("invalid identity must not call network"), cache_dir=tmp_path)
    with pytest.raises(ValueError):
        client.contract(bad_chain, bad_address)


def test_response_identity_mismatch_stops(tmp_path):
    payload = fixture_payload()
    payload["chainId"] = "1"
    client = sourcify.SourcifyClient(opener=opener_for(payload), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="IDENTITY_MISMATCH"):
        client.contract(CHAIN, ADDRESS)


@pytest.mark.parametrize("body", [b"not-json", b"null", b"NaN", b"{}"])
def test_malformed_data_stops(body, tmp_path):
    client = sourcify.SourcifyClient(opener=opener_for(body), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="MALFORMED_DATA"):
        client.contract(CHAIN, ADDRESS)


def test_oversized_and_non_json_responses_stop(tmp_path):
    oversized = sourcify.SourcifyClient(opener=opener_for(b"x" * (sourcify.MAX_RESPONSE_BYTES + 1)), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="RESPONSE_TOO_LARGE"):
        oversized.contract(CHAIN, ADDRESS)
    wrong_type = sourcify.SourcifyClient(opener=opener_for(fixture_payload(), content_type="text/html"), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="MALFORMED_DATA"):
        wrong_type.contract(CHAIN, ADDRESS)


def test_off_host_final_url_and_corrupt_cache_fail_closed(tmp_path):
    redirected = sourcify.SourcifyClient(opener=opener_for(fixture_payload(), url="https://other.example/x"), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="UNSAFE_REDIRECT"):
        redirected.contract(CHAIN, ADDRESS)
    path = redirected._cache_path(CHAIN, ADDRESS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    corrupt = sourcify.SourcifyClient(opener=lambda *a, **k: pytest.fail("corrupt cache must not cause refetch"), cache_dir=tmp_path)
    with pytest.raises(sourcify.ProviderError, match="CACHE_CORRUPT"):
        corrupt.contract(CHAIN, ADDRESS)
