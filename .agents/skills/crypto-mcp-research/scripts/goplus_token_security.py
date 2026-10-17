"""Bounded anonymous read-only GoPlus EVM and Solana token-security query."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

BASE_URL = "https://api.gopluslabs.io"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 500_000
DEFAULT_TIMEOUT_SECONDS = 10
CHAIN_ID = re.compile(r"^[0-9]{1,10}$")
EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_solana_address(value: str) -> bool:
    if not isinstance(value, str) or not SOLANA_ADDRESS.fullmatch(value):
        return False
    decoded = 0
    for character in value:
        decoded = decoded * 58 + BASE58_ALPHABET.index(character)
    decoded_bytes = (decoded.bit_length() + 7) // 8
    leading_zero_bytes = len(value) - len(value.lstrip("1"))
    return decoded_bytes + leading_zero_bytes == 32


def _request(chain_id: str, contract_address: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> tuple[str, Any]:
    if chain_id == "solana":
        if not _is_solana_address(contract_address):
            raise ValueError("contract_address must be a 32-byte Solana mint in base58")
        path = "/api/v1/solana/token_security"
    else:
        if not isinstance(chain_id, str) or not CHAIN_ID.fullmatch(chain_id):
            raise ValueError("chain_id must be a numeric EVM chain ID or 'solana'")
        if not isinstance(contract_address, str) or not EVM_ADDRESS.fullmatch(contract_address):
            raise ValueError("contract_address must be a 20-byte EVM address")
        path = f"/api/v1/token_security/{urllib.parse.quote(chain_id, safe='')}"
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    query = urllib.parse.urlencode({"contract_addresses": contract_address})
    url = f"{BASE_URL}{path}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if not 200 <= status_code < 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 402: "PAYMENT_REQUIRED", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; stop this provider branch") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
            raise ProviderError("TIMEOUT", "provider request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderError("MALFORMED_DATA", "response was not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ProviderError("MALFORMED_DATA", "expected an object response")
    return url, payload


def _extract_result(payload: dict[str, Any], contract_address: str, *, case_sensitive: bool = False) -> tuple[str, dict[str, Any] | None]:
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response code is missing or invalid") from None
    if code != 1:
        error_status = {
            2: "PARTIAL_DATA",
            2004: "INVALID_CONTRACT_ADDRESS",
            2018: "UNSUPPORTED_CHAIN",
            2020: "NOT_A_CONTRACT",
            2021: "NO_SECURITY_DATA",
            2022: "UNSUPPORTED_CHAIN",
            4023: "AUTH_REQUIRED",
            4029: "RATE_LIMITED",
        }.get(code, "PROVIDER_ERROR")
        raise ProviderError(error_status, f"GoPlus response code {code}: {payload.get('message', 'no message')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ProviderError("MALFORMED_DATA", "result must be an object")
    matches = lambda address: address == contract_address if case_sensitive else address.lower() == contract_address.lower()
    matching = [value for address, value in result.items()
                if isinstance(address, str) and matches(address)]
    if not matching:
        if result:
            raise ProviderError("IDENTITY_MISMATCH", "response contained a result for a different contract")
        return "NO_SECURITY_DATA", None
    if len(matching) != 1 or not isinstance(matching[0], dict):
        raise ProviderError("MALFORMED_DATA", "security result key/value shape is invalid")
    return "RESPONDED", matching[0]


@dataclass
class GoPlusClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 300
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be between 0 and 3600 seconds")
        self._cache: dict[tuple[str, str], tuple[float, str, Any, str]] = {}

    def token_security(self, chain_id: str, contract_address: str) -> dict[str, Any]:
        if chain_id == "solana":
            if not _is_solana_address(contract_address):
                raise ValueError("contract_address must be a 32-byte Solana mint in base58")
            cache_address = contract_address
        else:
            if not isinstance(chain_id, str) or not CHAIN_ID.fullmatch(chain_id):
                raise ValueError("chain_id must be a numeric EVM chain ID or 'solana'")
            if not isinstance(contract_address, str) or not EVM_ADDRESS.fullmatch(contract_address):
                raise ValueError("contract_address must be a 20-byte EVM address")
            cache_address = contract_address.lower()
        key = (chain_id, cache_address)
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds > 0 and cached and now - cached[0] <= self.cache_ttl_seconds:
            url, payload, retrieved, cache_state = cached[1], cached[2], cached[3], "hit"
        else:
            url, payload = _request(chain_id, contract_address, timeout=self.timeout, opener=self.opener)
            retrieved, cache_state = self.utc_clock(), "miss"
            self._cache[key] = (now, url, payload, retrieved)
        status, record = _extract_result(payload, contract_address, case_sensitive=chain_id == "solana")
        if status == "NO_SECURITY_DATA":
            record = None
        return {
            "provider": "goplus",
            "upstream_origin": "goplus_token_security_engine_lineage_unspecified",
            "endpoint": "GET /api/v1/solana/token_security" if chain_id == "solana" else "GET /api/v1/token_security/{chain_id}",
            "source_url": url,
            "chain_id": chain_id,
            "contract_address": contract_address,
            "retrieved_at_utc": retrieved,
            "observation_timestamp": None,
            "freshness_status": "UNVERIFIED_NO_PROVIDER_TIMESTAMP" if record is not None else "NO_SECURITY_DATA",
            "response_status": status,
            "security_fields": record,
            "missing_fields_are_unknown": True,
            "interpretation": "Automated heuristic screening only; not a contract audit, guarantee of safety, or sufficient basis for execution.",
            "cache": cache_state,
            "raw_response": payload,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", required=True, help="exact GoPlus EVM chain ID (for example 1) or 'solana'")
    parser.add_argument("--contract", required=True, help="20-byte EVM contract or exact Solana mint address")
    args = parser.parse_args(argv)
    try:
        result = GoPlusClient().token_security(args.chain, args.contract)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "goplus", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
