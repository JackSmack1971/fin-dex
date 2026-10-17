"""Bounded anonymous read-only client for DexPaprika's free public REST API."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

BASE_URL = "https://api.dexpaprika.com"
ROUTE_CONTRACT = Path(__file__).resolve().parents[4] / ".codex" / "dexpaprika-routing.json"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 10
NETWORK = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
POOL = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ADDRESS_PATH = r"(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})"
POOL_PATH = re.compile(rf"^/networks/([a-z0-9][a-z0-9-]{{0,49}})/pools/({ADDRESS_PATH})$")
TOKEN_POOLS_PATH = re.compile(
    rf"^/networks/([a-z0-9][a-z0-9-]{{0,49}})/pools/search\?token_address=({ADDRESS_PATH})&limit=(\d{{1,3}})&detailed=true$"
)
POOL_OHLCV_PATH = re.compile(
    rf"^/networks/([a-z0-9][a-z0-9-]{{0,49}})/pools/({ADDRESS_PATH})/ohlcv\?start=-24h&interval=(1h|6h|12h)&limit=(\d{{1,2}})$"
)
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _valid_address(network: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if network.lower() != "solana":
        return bool(POOL.fullmatch(value))
    if not BASE58_ADDRESS.fullmatch(value):
        return False
    number = 0
    for char in value:
        number = number * 58 + _BASE58_ALPHABET.index(char)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return len(b"\0" * leading_zeroes + decoded) == 32


def _same_address(network: str, left: str, right: str) -> bool:
    return left == right if network.lower() == "solana" else left.lower() == right.lower()


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assert_keyless_route_permitted() -> None:
    """Fail closed unless the saved route contract records successful requalification."""
    try:
        contract = json.loads(ROUTE_CONTRACT.read_text(encoding="utf-8"))
        status = contract["access"]["keyless_runtime_status"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        raise ProviderError("ROUTE_POLICY_UNAVAILABLE", "DexPaprika route contract is missing or invalid; no request sent") from None
    if status != "READY_ZERO_CHARGE_REQUALIFIED":
        raise ProviderError(
            "ROUTE_UNAVAILABLE",
            f"DexPaprika anonymous route is disabled by contract status {status!r}; no request sent",
        )


def _request(path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    match = POOL_PATH.fullmatch(path)
    token_pools_match = TOKEN_POOLS_PATH.fullmatch(path)
    ohlcv_match = POOL_OHLCV_PATH.fullmatch(path)
    if not match and not token_pools_match and not ohlcv_match:
        raise ValueError("path is outside the fixed pool-details, token-pool search, and keyless OHLCV routes")
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    _assert_keyless_route_permitted()
    request = urllib.request.Request(BASE_URL + path, headers={
        "Accept": "application/json", "User-Agent": USER_AGENT,
    }, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if status_code < 200 or status_code >= 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN",
                404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
            raise ProviderError("TIMEOUT", "provider request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")),
                           parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not valid UTF-8 JSON") from None
    if ohlcv_match:
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ProviderError("MALFORMED_DATA", "pool OHLCV response must be an array of candle objects")
    elif not isinstance(value, dict):
        raise ProviderError("MALFORMED_DATA", "pool response must be a JSON object")
    return value


@dataclass
class DexPaprikaClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 30
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be between 0 and 3600 seconds")
        self._cache: dict[str, tuple[float, dict[str, Any], str]] = {}

    def pool_snapshot(self, network: str, pool_address: str) -> dict[str, Any]:
        if not isinstance(network, str) or not NETWORK.fullmatch(network):
            raise ValueError("invalid network slug")
        if not _valid_address(network, pool_address):
            raise ValueError("pool_address must be a 20-byte EVM address or a valid Solana 32-byte public key")
        path = f"/networks/{network}/pools/{pool_address}"
        key = BASE_URL + path
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            data, retrieved = cached[1], cached[2]
            cache = "hit"
        else:
            data = _request(path, timeout=self.timeout, opener=self.opener)
            retrieved = self.utc_clock()
            cache = "miss"
            self._cache[key] = (now, data, retrieved)
        response_chain = data.get("chain")
        response_pool = data.get("id")
        if (not isinstance(response_chain, str) or not isinstance(response_pool, str)
                or response_chain.lower() != network.lower() or not _same_address(network, response_pool, pool_address)):
            raise ProviderError("IDENTITY_MISMATCH", "returned chain or pool does not match the request")
        tokens = data.get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(token, dict) and isinstance(token.get("id"), str) for token in tokens):
            raise ProviderError("MALFORMED_DATA", "pool response did not include identifiable tokens")
        token_ids = [token["id"] for token in tokens]
        base_id, quote_id = data.get("base_token_id"), data.get("quote_token_id")
        if (not _valid_address(network, base_id) or not _valid_address(network, quote_id)
                or _same_address(network, base_id, quote_id)
                or not any(_same_address(network, base_id, token_id) for token_id in token_ids)
                or not any(_same_address(network, quote_id, token_id) for token_id in token_ids)):
            raise ProviderError("IDENTITY_MISMATCH", "pool base/quote contracts do not match returned token identities")
        return {
            "provider": "dexpaprika",
            "upstream_origin": "dexpaprika_public_indexer",
            "endpoint": "GET /networks/{network}/pools/{pool_address}",
            "source_url": key,
            "requested_network": network,
            "requested_pool_address": pool_address,
            "retrieved_at_utc": retrieved,
            "observation_timestamps": {
                "price_time": data.get("price_time"),
                "last_updated": data.get("last_updated"),
            },
            "cache": cache,
            "data": data,
            "caveats": [
                "price_time and last_updated are preserved as returned; they are not assumed to timestamp liquidity or 24h volume",
                "liquidity_usd is pool liquidity, not executable depth; volume windows are turnover, not capacity",
                "upstream independence from any comparator must be established separately",
            ],
        }

    def token_pools(self, network: str, token_address: str, *, limit: int = 100) -> dict[str, Any]:
        """Return one bounded page of pools containing the exact token address."""
        if not isinstance(network, str) or not NETWORK.fullmatch(network):
            raise ValueError("invalid network slug")
        if not _valid_address(network, token_address):
            raise ValueError("token_address must be a 20-byte EVM address or a valid Solana 32-byte public key")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be within [1, 100]")
        path = f"/networks/{network}/pools/search?token_address={token_address}&limit={limit}&detailed=true"
        key = BASE_URL + path
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            payload, retrieved = cached[1], cached[2]
            cache = "hit"
        else:
            payload = _request(path, timeout=self.timeout, opener=self.opener)
            retrieved = self.utc_clock()
            cache = "miss"
            self._cache[key] = (now, payload, retrieved)
        results = payload.get("results")
        if not isinstance(results, list):
            raise ProviderError("MALFORMED_DATA", "token-pool search omitted results array")
        for row in results:
            if not isinstance(row, dict):
                raise ProviderError("MALFORMED_DATA", "token-pool search result must be an object")
            returned_chain = row.get("chain")
            pool_id = row.get("id")
            tokens = row.get("tokens")
            if not all(isinstance(value, str) for value in (returned_chain, pool_id)):
                raise ProviderError("MALFORMED_DATA", "pool search row omitted chain or pool identity")
            if returned_chain.lower() != network.lower():
                raise ProviderError("IDENTITY_MISMATCH", "pool search returned a different network")
            if not _valid_address(network, pool_id):
                raise ProviderError("IDENTITY_MISMATCH", "pool search returned an invalid pool identity for the requested network")
            if not isinstance(tokens, list) or len(tokens) < 2 or not all(
                isinstance(token, dict) and isinstance(token.get("id"), str) for token in tokens
            ):
                raise ProviderError("MALFORMED_DATA", "detailed pool search omitted identifiable token records")
            token_ids = [token["id"] for token in tokens]
            if not all(_valid_address(network, token_id) for token_id in token_ids):
                raise ProviderError("IDENTITY_UNVERIFIED", "returned token IDs are not valid addresses for the requested network")
            if not any(_same_address(network, token_address, token_id) for token_id in token_ids):
                raise ProviderError("IDENTITY_MISMATCH", "pool search row does not contain the requested token")
        return {
            "provider": "dexpaprika",
            "upstream_origin": "dexpaprika_public_indexer",
            "endpoint": "GET /networks/{network}/pools/search?token_address={token_address}&limit={limit}",
            "source_url": key,
            "requested_network": network,
            "requested_token_address": token_address,
            "retrieved_at_utc": retrieved,
            "cache": cache,
            "pair_count": len(results),
            "coverage_status": "FIRST_PAGE_ONLY" if payload.get("has_next_page") else "RESPONSE_PAGE_COMPLETE",
            "has_next_page": payload.get("has_next_page"),
            "next_cursor": payload.get("next_cursor"),
            "data": payload,
            "caveats": [
                "Only the first bounded search page is returned; has_next_page and next_cursor are preserved.",
                "Pool liquidity/TVL is not executable depth; volume is turnover.",
                "Upstream independence from DEX Screener remains unknown.",
            ],
        }

    def pool_ohlcv(self, network: str, pool_address: str, *, start: str = "-24h",
                   interval: str = "1h", limit: int = 24) -> dict[str, Any]:
        """Fetch one bounded keyless candle window within the documented no-key plan."""
        if not isinstance(network, str) or not NETWORK.fullmatch(network):
            raise ValueError("invalid network slug")
        if not _valid_address(network, pool_address):
            raise ValueError("pool_address must be a 20-byte EVM address or a valid Solana 32-byte public key")
        if start != "-24h":
            raise ValueError("keyless history is bounded to the last 24 hours")
        if interval not in {"1h", "6h", "12h"}:
            raise ValueError("keyless intervals are limited to 1h, 6h, or 12h")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 24:
            raise ValueError("limit must be within [1, 24]")
        path = f"/networks/{network}/pools/{pool_address}/ohlcv?start={start}&interval={interval}&limit={limit}"
        key = BASE_URL + path
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            candles, retrieved = cached[1], cached[2]
            cache = "hit"
        else:
            candles = _request(path, timeout=self.timeout, opener=self.opener)
            retrieved = self.utc_clock()
            cache = "miss"
            self._cache[key] = (now, candles, retrieved)
        if len(candles) > limit:
            raise ProviderError("MALFORMED_DATA", "OHLCV response exceeded the requested candle limit")
        required = {"time_open", "time_close", "open", "high", "low", "close", "volume"}
        for candle in candles:
            if not required.issubset(candle):
                raise ProviderError("MALFORMED_DATA", "OHLCV candle omitted a documented field")
            for field in ("open", "high", "low", "close", "volume"):
                value = candle[field]
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    raise ProviderError("MALFORMED_DATA", f"OHLCV {field} must be finite numeric data")
            if not isinstance(candle["time_open"], str) or not isinstance(candle["time_close"], str):
                raise ProviderError("MALFORMED_DATA", "OHLCV candle timestamps must be strings")
        return {
            "provider": "dexpaprika",
            "upstream_origin": "dexpaprika_public_indexer",
            "endpoint": "GET /networks/{network}/pools/{pool_address}/ohlcv",
            "source_url": key,
            "requested_network": network,
            "requested_pool_address": pool_address,
            "retrieved_at_utc": retrieved,
            "cache": cache,
            "identity_status": "REQUEST_ROUTE_MATCHED_RESPONSE_HAS_NO_POOL_ENVELOPE",
            "coverage_status": "BOUNDED_24H_RESPONSE; candles may be absent for no-trade intervals",
            "candle_interval": interval,
            "candle_count": len(candles),
            "data": candles,
            "caveats": [
                "Keyless plan window is limited to the last 24 hours and hourly or coarser intervals; no paid or credentialed route is used.",
                "Candle price orientation and quote denomination must be checked against the exact pool token identities before treating prices as USD.",
                "A pool candle series is pool-specific and does not substitute for a token-wide price series across venues.",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pool-snapshot", "pool-ohlcv", "token-pools"))
    parser.add_argument("--network", required=True)
    parser.add_argument("--pool-address")
    parser.add_argument("--token-address")
    parser.add_argument("--start", default="-24h")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        client = DexPaprikaClient()
        if args.command == "token-pools":
            if not args.token_address or args.pool_address:
                parser.error("token-pools requires --token-address and does not accept --pool-address")
            result = client.token_pools(args.network, args.token_address,
                                        limit=100 if args.limit is None else args.limit)
        else:
            if not args.pool_address or args.token_address:
                parser.error(f"{args.command} requires --pool-address and does not accept --token-address")
            result = (client.pool_snapshot(args.network, args.pool_address) if args.command == "pool-snapshot"
                      else client.pool_ohlcv(args.network, args.pool_address, start=args.start,
                                             interval=args.interval,
                                             limit=24 if args.limit is None else args.limit))
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "dexpaprika", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
