"""Bounded anonymous read-only client for Hyperliquid perp market observations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

BASE_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 1_000_000
MAX_FUNDING_WINDOW_MS = 7 * 86_400_000
MAX_FUNDING_ROWS = 200
COIN = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")


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


def _request(body: dict[str, Any], *, timeout: float = 10,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> Any:
    valid_funding = body.get("type") == "fundingHistory" and set(body) == {"type", "coin", "startTime", "endTime"}
    valid_snapshot = body == {"type": "metaAndAssetCtxs"}
    if not (valid_funding or valid_snapshot):
        raise ValueError("only fixed fundingHistory and metaAndAssetCtxs info operations are allowed")
    payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
    request = urllib.request.Request(BASE_URL, data=payload, headers={
        "Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT,
    }, method="POST")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if status_code < 200 or status_code >= 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        status = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
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
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")), parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not finite UTF-8 JSON") from None
    if not isinstance(value, list):
        raise ProviderError("MALFORMED_DATA", "fundingHistory response must be an array")
    return value


@dataclass
class HyperliquidClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = 10
    cache_ttl_seconds: int = 30
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if not 0 < self.timeout <= 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be within [0, 3600] seconds")
        self._cache: dict[tuple[str, int, int], tuple[float, list[dict[str, Any]], str]] = {}

    def funding_history(self, coin: str, start_time_ms: int, end_time_ms: int) -> dict[str, Any]:
        if not isinstance(coin, str) or not COIN.fullmatch(coin):
            raise ValueError("invalid exact Hyperliquid perpetual coin identifier")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (start_time_ms, end_time_ms)):
            raise ValueError("start and end must be nonnegative integer Unix milliseconds")
        if end_time_ms <= start_time_ms or end_time_ms - start_time_ms > MAX_FUNDING_WINDOW_MS:
            raise ValueError("history window must be positive and at most 7 days")
        if end_time_ms > int(time.time() * 1000) + 60_000:
            raise ValueError("end time cannot be in the future")
        key = (coin, start_time_ms, end_time_ms)
        now = self.clock()
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            rows, retrieved, state = cached[1], cached[2], "hit"
        else:
            body = {"type": "fundingHistory", "coin": coin, "startTime": start_time_ms, "endTime": end_time_ms}
            rows = _request(body, timeout=self.timeout, opener=self.opener)
            retrieved, state = self.utc_clock(), "miss"
            self._cache[key] = (now, rows, retrieved)
        normalized = []
        for row in rows:
            if not isinstance(row, dict) or row.get("coin") != coin:
                raise ProviderError("IDENTITY_MISMATCH", "funding row coin does not match the exact requested instrument")
            ts = row.get("time")
            if not isinstance(ts, int) or isinstance(ts, bool) or not start_time_ms <= ts <= end_time_ms:
                raise ProviderError("MALFORMED_DATA", "funding row has invalid or out-of-window Unix millisecond timestamp")
            raw_rate = row.get("fundingRate")
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                raise ProviderError("MALFORMED_DATA", "fundingRate is not numeric") from None
            if not math.isfinite(rate):
                raise ProviderError("MALFORMED_DATA", "fundingRate must be finite")
            item = {"coin": coin, "time_ms": ts, "funding_rate_decimal": rate, "funding_rate_raw": raw_rate}
            if "premium" in row:
                item["premium_raw"] = row["premium"]
            normalized.append(item)
        if len(normalized) > MAX_FUNDING_ROWS:
            raise ProviderError("MALFORMED_DATA", f"fundingHistory exceeded the bounded {MAX_FUNDING_ROWS}-row response limit")
        normalized.sort(key=lambda item: item["time_ms"])
        return {
            "provider": "hyperliquid_public_info",
            "upstream_origin": "Hyperliquid first-party perp market data",
            "endpoint": "POST /info type=fundingHistory",
            "source_url": BASE_URL,
            "requested_coin": coin,
            "requested_window_ms": {"start": start_time_ms, "end": end_time_ms},
            "retrieved_at_utc": retrieved,
            "cache": state,
            "row_count": len(normalized),
            "funding_interval_note": "Hyperliquid documents hourly funding settlement; retain each returned timestamp and do not extrapolate missing intervals. The request window is locally bounded to seven days.",
            "unit": "funding_rate_decimal per funding period, as returned; not annualized",
            "data": normalized,
        }

    def perp_snapshot(self, coin: str) -> dict[str, Any]:
        """Return current funding and OI context for one exact perp coin."""
        if not isinstance(coin, str) or not COIN.fullmatch(coin):
            raise ValueError("invalid exact Hyperliquid perpetual coin identifier")
        now = self.clock()
        key = (coin, -1, -1)
        cached = self._cache.get(key)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            payload, retrieved, state = cached[1], cached[2], "hit"
        else:
            payload = _request({"type": "metaAndAssetCtxs"}, timeout=self.timeout, opener=self.opener)
            retrieved, state = self.utc_clock(), "miss"
            if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[0], dict) or not isinstance(payload[1], list):
                raise ProviderError("MALFORMED_DATA", "metaAndAssetCtxs must return [metadata, asset contexts]")
            self._cache[key] = (now, payload, retrieved)
        meta, contexts = payload
        universe = meta.get("universe")
        if not isinstance(universe, list) or len(universe) != len(contexts):
            raise ProviderError("MALFORMED_DATA", "metadata universe and asset context lengths do not align")
        matches = [index for index, asset in enumerate(universe)
                   if isinstance(asset, dict) and asset.get("name") == coin]
        if len(matches) != 1:
            raise ProviderError("IDENTITY_MISMATCH", "requested coin is absent or ambiguous in returned perp universe")
        context = contexts[matches[0]]
        if not isinstance(context, dict):
            raise ProviderError("MALFORMED_DATA", "requested perp asset context is not an object")
        values = {}
        for field in ("openInterest", "funding", "markPx", "oraclePx", "dayNtlVlm"):
            raw = context.get(field)
            if raw is None:
                values[field] = None
                continue
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                raise ProviderError("MALFORMED_DATA", f"{field} is not numeric") from None
            if not math.isfinite(parsed):
                raise ProviderError("MALFORMED_DATA", f"{field} must be finite")
            values[field] = {"raw": raw, "numeric": parsed}
        return {
            "provider": "hyperliquid_public_info",
            "upstream_origin": "Hyperliquid first-party perp market data",
            "endpoint": "POST /info type=metaAndAssetCtxs",
            "source_url": BASE_URL,
            "requested_coin": coin,
            "retrieved_at_utc": retrieved,
            "observation_timestamp": None,
            "cache": state,
            "data": values,
            "unit_note": "Open-interest unit is preserved as the provider returns it; this endpoint documentation does not establish USD denomination or provide an observation timestamp.",
            "coverage_status": "CURRENT_SNAPSHOT_ONLY; not historical OI",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    funding = sub.add_parser("funding-history")
    funding.add_argument("coin")
    funding.add_argument("--start-ms", type=int, required=True)
    funding.add_argument("--end-ms", type=int, required=True)
    snapshot = sub.add_parser("perp-snapshot")
    snapshot.add_argument("coin")
    args = parser.parse_args(argv)
    try:
        client = HyperliquidClient()
        result = (client.funding_history(args.coin, args.start_ms, args.end_ms)
                  if args.command == "funding-history" else client.perp_snapshot(args.coin))
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "hyperliquid_public_info", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
