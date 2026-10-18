"""Bounded, anonymous, read-only client for DeFiLlama's documented free API.

This module intentionally supports only fixed GET routes on DeFiLlama's free
API hosts. It does not accept credentials, arbitrary hosts, custom paths, or
request bodies.
"""
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

BASE_URL = "https://api.llama.fi"
STABLECOIN_BASE_URL = "https://stablecoins.llama.fi"
ALLOWED_BASE_URLS = {BASE_URL, STABLECOIN_BASE_URL}
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 10
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,99}$")
PATH_SEGMENT = r"(?:[A-Za-z0-9._-]|%[0-9A-Fa-f]{2})+"
ALLOWED_PATHS = re.compile(
    rf"(?:/protocols|/protocol/{PATH_SEGMENT}|/tvl/{PATH_SEGMENT}|"
    rf"/overview/fees(?:/{PATH_SEGMENT})?|/summary/fees/{PATH_SEGMENT}|/overview/open-interest|/stablecoins|/stablecoinchains|"
    rf"/stablecoincharts/(?:all|{PATH_SEGMENT})|/v2/historicalChainTvl(?:/{PATH_SEGMENT})?)$"
)
STABLECOIN_PATHS = re.compile(rf"(?:/stablecoins|/stablecoinchains|/stablecoincharts/(?:all|{PATH_SEGMENT}))$")


class ProviderError(Exception):
    """A classified provider failure that must not be silently retried."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _safe_slug(value: str, label: str) -> str:
    if not isinstance(value, str) or not SLUG.fullmatch(value) or ".." in value:
        raise ValueError(f"invalid {label}")
    return urllib.parse.quote(value, safe="-._")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finite_float(raw: str) -> float:
    parsed = float(raw)
    if not math.isfinite(parsed):
        raise ValueError("non-finite number")
    return parsed


def freshness_status(observed_at_utc: str | None, *, now_utc: dt.datetime,
                     max_age_seconds: int) -> str:
    """Classify current observations; historical series should retain their dates."""
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be nonnegative")
    if not observed_at_utc:
        return "UNVERIFIED"
    try:
        observed = dt.datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "UNVERIFIED"
    if observed.tzinfo is None or now_utc.tzinfo is None:
        return "UNVERIFIED"
    age = (now_utc - observed).total_seconds()
    if age < 0:
        return "UNVERIFIED"
    return "FRESH" if age <= max_age_seconds else "STALE"


def sources_are_independent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Require explicit, distinct upstream identifiers; wrapper names do not count."""
    left_origin = left.get("upstream_origin")
    right_origin = right.get("upstream_origin")
    return isinstance(left_origin, str) and bool(left_origin.strip()) and isinstance(right_origin, str) and bool(right_origin.strip()) and left_origin != right_origin


def _request(path: str, *, query: dict[str, str] | None = None,
             base_url: str = BASE_URL,
             timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> Any:
    """Fetch exactly one bounded public GET; injectable opener supports fixtures."""
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    valid_host_route = (
        base_url == BASE_URL and ALLOWED_PATHS.fullmatch(path) and not path.startswith(("/stablecoins", "/stablecoinchains", "/stablecoincharts/"))
    ) or (
        base_url == STABLECOIN_BASE_URL and STABLECOIN_PATHS.fullmatch(path)
    )
    if base_url not in ALLOWED_BASE_URLS or not valid_host_route or ".." in path:
        raise ValueError("path is outside the fixed API origin")
    url = base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
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
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")), parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not valid UTF-8 JSON") from None
    if not isinstance(value, (dict, list, int, float)) or isinstance(value, bool):
        raise ProviderError("MALFORMED_DATA", "response JSON had an unsupported top-level type")
    if isinstance(value, float) and not math.isfinite(value):
        raise ProviderError("MALFORMED_DATA", "response JSON contained a non-finite number")
    return value


@dataclass
class DefiLlamaClient:
    """One request per method call; cache is process-local and freshness-aware."""
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
        self._cache: dict[str, tuple[float, Any, str]] = {}

    def get(self, endpoint: str, *, path: str, query: dict[str, str] | None = None,
            base_url: str = BASE_URL) -> dict[str, Any]:
        if base_url not in ALLOWED_BASE_URLS:
            raise ValueError("base URL is outside the fixed API origins")
        key = base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds > 0 and cached and now - cached[0] <= self.cache_ttl_seconds:
            return {"provider": "defillama", "upstream_origin": "defillama_aggregated_index", "endpoint": endpoint, "source_url": key,
                    "retrieved_at_utc": cached[2], "cache": "hit", "data": cached[1]}
        data = _request(path, query=query, base_url=base_url, timeout=self.timeout, opener=self.opener)
        retrieved = self.utc_clock()
        self._cache[key] = (now, data, retrieved)
        return {"provider": "defillama", "upstream_origin": "defillama_aggregated_index", "endpoint": endpoint, "source_url": key,
                "retrieved_at_utc": retrieved, "cache": "miss", "data": data}

    def protocols(self) -> dict[str, Any]:
        return self.get("GET /protocols", path="/protocols")

    def protocol_tvl(self, slug: str) -> dict[str, Any]:
        return self.get("GET /tvl/{protocol}", path=f"/tvl/{_safe_slug(slug, 'protocol slug')}")

    def protocol_history(self, slug: str) -> dict[str, Any]:
        return self.get("GET /protocol/{protocol}", path=f"/protocol/{_safe_slug(slug, 'protocol slug')}")

    def fees(self, *, data_type: str = "dailyFees", chain: str | None = None) -> dict[str, Any]:
        if data_type not in {"dailyFees", "dailyRevenue", "dailyHoldersRevenue"}:
            raise ValueError("unsupported fees dataType")
        path = "/overview/fees" + (f"/{_safe_slug(chain, 'chain')}" if chain is not None else "")
        return self.get("GET /overview/fees[/{chain}]", path=path,
                        query={"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true", "dataType": data_type})

    def protocol_fees(self, protocol: str, *, data_type: str = "dailyFees") -> dict[str, Any]:
        """Fetch one protocol's summary and dated historical chart."""
        if data_type not in {"dailyFees", "dailyRevenue", "dailyHoldersRevenue"}:
            raise ValueError("unsupported fees dataType")
        return self.get("GET /summary/fees/{protocol}",
                        path=f"/summary/fees/{_safe_slug(protocol, 'protocol slug')}",
                        query={"dataType": data_type})

    def open_interest(self) -> dict[str, Any]:
        """Fetch DeFiLlama's aggregate dated open-interest overview."""
        return self.get("GET /overview/open-interest", path="/overview/open-interest",
                        query={"excludeTotalDataChart": "false", "excludeTotalDataChartBreakdown": "true"})

    def stablecoins(self, *, include_prices: bool = False) -> dict[str, Any]:
        return self.get("GET /stablecoins", path="/stablecoins", query={"includePrices": str(include_prices).lower()}, base_url=STABLECOIN_BASE_URL)

    def stablecoin_chains(self) -> dict[str, Any]:
        return self.get("GET /stablecoinchains", path="/stablecoinchains", base_url=STABLECOIN_BASE_URL)

    def stablecoin_history(self, *, chain: str | None = None, stablecoin_id: int | None = None) -> dict[str, Any]:
        if stablecoin_id is not None and (isinstance(stablecoin_id, bool) or stablecoin_id <= 0):
            raise ValueError("stablecoin_id must be a positive integer")
        path = "/stablecoincharts/" + _safe_slug(chain, "chain") if chain is not None else "/stablecoincharts/all"
        query = {"stablecoin": str(stablecoin_id)} if stablecoin_id is not None else None
        return self.get("GET /stablecoincharts/{chain|all}", path=path, query=query, base_url=STABLECOIN_BASE_URL)

    def historical_chain_tvl(self, *, chain: str | None = None) -> dict[str, Any]:
        path = "/v2/historicalChainTvl" + (f"/{_safe_slug(chain, 'chain')}" if chain is not None else "")
        return self.get("GET /v2/historicalChainTvl[/{chain}]", path=path)


COMMANDS = {
    "protocols": lambda c, a: c.protocols(),
    "protocol-tvl": lambda c, a: c.protocol_tvl(a.protocol),
    "protocol-history": lambda c, a: c.protocol_history(a.protocol),
    "fees": lambda c, a: c.fees(data_type=a.data_type, chain=a.chain),
    "protocol-fees": lambda c, a: c.protocol_fees(a.protocol, data_type=a.data_type),
    "open-interest": lambda c, a: c.open_interest(),
    "stablecoins": lambda c, a: c.stablecoins(include_prices=a.include_prices),
    "stablecoin-chains": lambda c, a: c.stablecoin_chains(),
    "stablecoin-history": lambda c, a: c.stablecoin_history(chain=a.chain, stablecoin_id=a.stablecoin_id),
    "chain-tvl-history": lambda c, a: c.historical_chain_tvl(chain=a.chain),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--protocol")
    parser.add_argument("--chain")
    parser.add_argument("--data-type", choices=("dailyFees", "dailyRevenue", "dailyHoldersRevenue"), default="dailyFees")
    parser.add_argument("--stablecoin-id", type=int)
    parser.add_argument("--include-prices", action="store_true")
    args = parser.parse_args(argv)
    if args.command in {"protocol-history", "protocol-tvl", "protocol-fees"} and not args.protocol:
        parser.error(f"{args.command} requires --protocol")
    try:
        result = COMMANDS[args.command](DefiLlamaClient(), args)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "defillama", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    # Escaping non-ASCII keeps valid machine-readable JSON on Windows consoles
    # whose active code page cannot encode arbitrary provider text.
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
