"""Bounded anonymous read-only Binance USDⓈ-M historical open-interest client."""
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

BASE_URL = "https://fapi.binance.com"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 500_000
SYMBOL = re.compile(r"^[A-Z0-9_]{2,32}$")
PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
PERIOD_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
             "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
             "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000}


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _request(symbol: str, period: str, limit: int, *, timeout: float = 10,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> list[Any]:
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("symbol must be an exact uppercase Binance USDⓈ-M symbol")
    if period not in PERIODS:
        raise ValueError(f"period must be one of: {', '.join(sorted(PERIODS))}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if not 0 < timeout <= 30 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive and bounded")
    query = urllib.parse.urlencode({"symbol": symbol, "period": period, "limit": limit})
    url = f"{BASE_URL}/futures/data/openInterestHist?{query}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": USER_AGENT,
    }, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if status_code < 200 or status_code >= 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        status = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED", 451: "REGION_UNAVAILABLE"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(status, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
            raise ProviderError("TIMEOUT", "provider request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8"),
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")),
                           parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not finite UTF-8 JSON") from None
    if not isinstance(value, list):
        raise ProviderError("MALFORMED_DATA", "open-interest history response must be an array")
    return value


@dataclass
class BinanceFuturesClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = 10
    cache_ttl_seconds: int = 30
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if not 0 < self.timeout <= 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 600:
            raise ValueError("cache TTL must be within [0, 600] seconds")
        self._cache: dict[tuple[str, str, int], tuple[float, list[Any], str]] = {}

    def open_interest_history(self, symbol: str, period: str, limit: int = 100) -> dict[str, Any]:
        # Validate before cache lookup or network access.
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise ValueError("symbol must be an exact uppercase Binance USDⓈ-M symbol")
        if period not in PERIODS:
            raise ValueError(f"period must be one of: {', '.join(sorted(PERIODS))}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        key = (symbol, period, limit)
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            rows, retrieved, state = cached[1], cached[2], "hit"
        else:
            rows = _request(symbol, period, limit, timeout=self.timeout, opener=self.opener)
            retrieved, state = self.utc_clock(), "miss"
            self._cache[key] = (now, rows, retrieved)
        if len(rows) > limit:
            raise ProviderError("MALFORMED_DATA", "provider returned more rows than the requested limit")
        try:
            retrieved_dt = dt.datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
            if retrieved_dt.tzinfo is None:
                raise ValueError("retrieval time lacks timezone")
        except (AttributeError, TypeError, ValueError):
            raise ProviderError("MALFORMED_DATA", "retrieval timestamp must be timezone-aware ISO-8601") from None
        normalized = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("symbol") != symbol:
                raise ProviderError("IDENTITY_MISMATCH", "OI row symbol does not match the requested exact instrument")
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0 or timestamp in seen:
                raise ProviderError("MALFORMED_DATA", "OI row timestamp is invalid or duplicated")
            if timestamp > int(retrieved_dt.timestamp() * 1000) + max(PERIOD_MS[period] * 2, 300_000):
                raise ProviderError("MALFORMED_DATA", "OI row timestamp is implausibly later than retrieval time")
            seen.add(timestamp)
            values = {}
            for field in ("sumOpenInterest", "sumOpenInterestValue"):
                raw = row.get(field)
                if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
                    raise ProviderError("MALFORMED_DATA", f"{field} is missing or non-numeric")
                try:
                    parsed = float(raw)
                except (ValueError, TypeError):
                    raise ProviderError("MALFORMED_DATA", f"{field} is non-numeric") from None
                if not math.isfinite(parsed) or parsed < 0:
                    raise ProviderError("MALFORMED_DATA", f"{field} must be finite and nonnegative")
                values[field] = {"raw": raw, "numeric": parsed}
            try:
                utc_timestamp = dt.datetime.fromtimestamp(timestamp / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
            except (OverflowError, OSError, ValueError):
                raise ProviderError("MALFORMED_DATA", "OI row timestamp is outside the supported datetime range") from None
            normalized.append({
                "symbol": symbol,
                "period": period,
                "time_ms": timestamp,
                "time_utc": utc_timestamp,
                **values,
            })
        normalized.sort(key=lambda item: item["time_ms"])
        stale_after_ms = max(PERIOD_MS[period] * 2, 60_000)
        latest_age_ms = (retrieved_dt.timestamp() * 1000 - normalized[-1]["time_ms"]) if normalized else None
        freshness = ("NO_OBSERVATION" if not normalized else
                     "STALE" if latest_age_ms > stale_after_ms else "FRESH")
        return {
            "provider": "binance_usdm_public",
            "upstream_origin": "Binance USDⓈ-M Futures first-party market data",
            "endpoint": "GET /futures/data/openInterestHist",
            "source_url": f"{BASE_URL}/futures/data/openInterestHist",
            "requested_symbol": symbol,
            "period": period,
            "requested_limit": limit,
            "retrieved_at_utc": retrieved,
            "cache": state,
            "row_count": len(normalized),
            "freshness_status": freshness,
            "latest_row_age_ms": latest_age_ms,
            "stale_threshold_ms": stale_after_ms,
            "coverage_status": "PROVIDER_DOCUMENTED_LATEST_30_DAYS; returned rows are bounded by requested limit",
            "units": {
                "sumOpenInterest": "Provider describes total open interest; unit/contract denomination not specified in this route response schema.",
                "sumOpenInterestValue": "Provider describes total open interest value; quote denomination is not specified in this route response schema.",
            },
            "data": normalized,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="exact Binance USDⓈ-M symbol, e.g. ETHUSDT")
    parser.add_argument("--period", choices=sorted(PERIODS), default="1h")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        result = BinanceFuturesClient().open_interest_history(args.symbol, args.period, args.limit)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "binance_usdm_public", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
