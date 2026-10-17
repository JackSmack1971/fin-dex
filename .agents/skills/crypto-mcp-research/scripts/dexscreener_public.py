"""Bounded, anonymous, read-only client for DEX Screener pair observations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

BASE_URL = "https://api.dexscreener.com"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 10
CHAIN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_:-]{2,150}$")
SEGMENT = r"[A-Za-z0-9_:-]{2,150}"
ALLOWED_PATHS = re.compile(
    rf"(?:/token-pairs/v1/[a-z0-9][a-z0-9_-]{{0,49}}/{SEGMENT}|"
    rf"/latest/dex/pairs/[a-z0-9][a-z0-9_-]{{0,49}}/{SEGMENT})$"
)


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identity(chain_id: str, address: str, label: str) -> tuple[str, str]:
    if not isinstance(chain_id, str) or not CHAIN.fullmatch(chain_id):
        raise ValueError("invalid chainId; use the exact DEX Screener chain identifier")
    if not isinstance(address, str) or not IDENTIFIER.fullmatch(address):
        raise ValueError(f"invalid {label}")
    return urllib.parse.quote(chain_id, safe="-_"), urllib.parse.quote(address, safe="_:-")


def _same_address(left: str, right: str) -> bool:
    if left.startswith("0x") and right.startswith("0x"):
        return left.lower() == right.lower()
    return left == right


def _request(path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> Any:
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    if not ALLOWED_PATHS.fullmatch(path):
        raise ValueError("path is outside the fixed DEX Screener read allowlist")
    request = urllib.request.Request(BASE_URL + path,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if status_code < 200 or status_code >= 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 402: "PAYMENT_REQUIRED", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
            raise ProviderError("TIMEOUT", "provider request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")),
            parse_float=lambda raw_float: _finite_float(raw_float))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not finite UTF-8 JSON") from None
    if not isinstance(payload, (list, dict)):
        raise ProviderError("MALFORMED_DATA", "expected an object or array response")
    return payload


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _check_pairs(payload: Any, chain_id: str, *, token: str | None = None,
                 pair_address: str | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if "pairs" not in payload:
            raise ProviderError("MALFORMED_DATA", "pair response omitted pairs field")
        pairs = payload.get("pairs")
        single_pair = payload.get("pair")
        if single_pair is not None:
            if not isinstance(single_pair, dict):
                raise ProviderError("MALFORMED_DATA", "pair field must be an object when present")
            _check_pairs([single_pair], chain_id, token=token, pair_address=pair_address)
        if pairs is None:
            return []
    else:
        pairs = payload
    if not isinstance(pairs, list):
        raise ProviderError("MALFORMED_DATA", "expected pair list in provider response")
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("chainId") != chain_id:
            raise ProviderError("IDENTITY_MISMATCH", "response chainId does not match the requested chain")
        if token is not None:
            base = pair.get("baseToken")
            quote = pair.get("quoteToken")
            addresses = [item.get("address") for item in (base, quote) if isinstance(item, dict)]
            if not any(isinstance(address, str) and _same_address(address, token) for address in addresses):
                raise ProviderError("IDENTITY_MISMATCH", "returned pair does not contain the requested token address")
        if pair_address is not None:
            returned = pair.get("pairAddress")
            if not isinstance(returned, str) or not _same_address(returned, pair_address):
                raise ProviderError("IDENTITY_MISMATCH", "response pair address does not match the request")
    return pairs


@dataclass
class DexScreenerClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 30
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 600:
            raise ValueError("cache TTL must be between 0 and 600 seconds")
        self._cache: dict[str, tuple[float, Any, str]] = {}

    def _get(self, endpoint: str, path: str, chain: str, identifier: str,
             *, token: str | None = None, pair_address: str | None = None) -> dict[str, Any]:
        now = self.clock()
        cached = self._cache.get(path)
        if self.cache_ttl_seconds > 0 and cached and now - cached[0] <= self.cache_ttl_seconds:
            payload, retrieved, state = cached[1], cached[2], "hit"
        else:
            payload = _request(path, timeout=self.timeout, opener=self.opener)
            _check_pairs(payload, chain, token=token, pair_address=pair_address)
            retrieved, state = self.utc_clock(), "miss"
            self._cache[path] = (now, payload, retrieved)
        # Re-check cached identities to prevent a mutated payload becoming trusted.
        pairs = _check_pairs(payload, chain, token=token, pair_address=pair_address)
        has_observations = bool(pairs)
        return {
            "provider": "dexscreener",
            "upstream_origin": "dexscreener_custom_blockchain_indexer",
            "endpoint": endpoint,
            "source_url": BASE_URL + path,
            "requested_chain_id": chain,
            "requested_identifier": identifier,
            "retrieved_at_utc": retrieved,
            "observation_timestamp": None,
            "observation_timestamp_note": "Pair response does not provide a timestamp for this liquidity/volume snapshot; pairCreatedAt is only the pair creation time.",
            "freshness_status": "UNVERIFIED_NO_OBSERVATION_TIMESTAMP" if has_observations else "NO_OBSERVATION",
            "identity_status": "VERIFIED" if has_observations else "NO_OBSERVATION",
            "cache": state,
            "pair_count": len(pairs),
            "data": payload,
        }

    def token_pairs(self, chain_id: str, token_address: str) -> dict[str, Any]:
        chain, address = _identity(chain_id, token_address, "token address")
        path = f"/token-pairs/v1/{chain}/{address}"
        return self._get("GET /token-pairs/v1/{chainId}/{tokenAddress}", path, chain_id,
                         token_address, token=token_address)

    def pair(self, chain_id: str, pair_address: str) -> dict[str, Any]:
        chain, address = _identity(chain_id, pair_address, "pair address")
        path = f"/latest/dex/pairs/{chain}/{address}"
        return self._get("GET /latest/dex/pairs/{chainId}/{pairId}", path, chain_id,
                         pair_address, pair_address=pair_address)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    token = sub.add_parser("token-pairs", help="get pools for one exact chain and token address")
    token.add_argument("--chain", required=True)
    token.add_argument("--token", required=True)
    pair = sub.add_parser("pair", help="get one exact pair by chain and pair address")
    pair.add_argument("--chain", required=True)
    pair.add_argument("--pair", required=True)
    args = parser.parse_args(argv)
    client = DexScreenerClient()
    try:
        result = client.token_pairs(args.chain, args.token) if args.command == "token-pairs" else client.pair(args.chain, args.pair)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "dexscreener", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
