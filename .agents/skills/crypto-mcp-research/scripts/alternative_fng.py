"""Bounded, anonymous, read-only Alternative.me Fear & Greed history client."""
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

BASE_URL = "https://api.alternative.me"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 100_000
DEFAULT_TIMEOUT_SECONDS = 10
MAX_LIMIT = 31


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_now(value: dt.datetime | str | None = None) -> dt.datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("now must include UTC timezone")
    return value.astimezone(dt.timezone.utc)


def _utc_string(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def freshness_status(observed_at_utc: str | None, *, now_utc: dt.datetime,
                     max_age_seconds: int = 172_800) -> str:
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


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _request(limit: int, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] = urllib.request.urlopen) -> tuple[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    query = urllib.parse.urlencode({"limit": str(limit), "format": "json"})
    url = f"{BASE_URL}/fng/?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if not 200 <= status_code < 300:
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
            parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not finite UTF-8 JSON") from None
    return url, payload


def _validate_payload(payload: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("name") != "Fear and Greed Index":
        raise ProviderError("MALFORMED_DATA", "unexpected Fear & Greed response envelope")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("error") not in (None, ""):
        raise ProviderError("PROVIDER_ERROR", "provider metadata reported an error")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or len(rows) > limit:
        raise ProviderError("MALFORMED_DATA", "data must be a nonempty array within the requested limit")
    parsed_rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    previous = None
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("MALFORMED_DATA", "data entries must be objects")
        try:
            value = int(row["value"])
            timestamp = int(row["timestamp"])
            observed = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ProviderError("MALFORMED_DATA", "value and Unix timestamp must be valid integers") from None
        if not 0 <= value <= 100 or timestamp <= 0 or timestamp in seen:
            raise ProviderError("MALFORMED_DATA", "value must be 0..100 and timestamps must be positive and unique")
        if previous is not None and timestamp >= previous:
            raise ProviderError("MALFORMED_DATA", "historical records must be strictly newest-first")
        classification = row.get("value_classification")
        if not isinstance(classification, str) or not classification.strip():
            raise ProviderError("MALFORMED_DATA", "value_classification is required")
        parsed_rows.append({
            "value": value,
            "unit": "index_points_0_to_100",
            "classification": classification,
            "observed_at_utc": _utc_string(observed),
            "provider_timestamp_unix": timestamp,
        })
        seen.add(timestamp)
        previous = timestamp
    return parsed_rows


@dataclass
class AlternativeFNGClient:
    opener: Callable[..., Any] = urllib.request.urlopen
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 3600
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], dt.datetime] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 86_400:
            raise ValueError("cache TTL must be between 0 and 86400 seconds")
        self._cache: dict[int, tuple[float, str, Any, str]] = {}

    def history(self, limit: int = 7) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        now = self.clock()
        cached = self._cache.get(limit)
        if self.cache_ttl_seconds and cached and now - cached[0] <= self.cache_ttl_seconds:
            url, payload, retrieved, cache_state = cached[1], cached[2], cached[3], "hit"
        else:
            url, payload = _request(limit, timeout=self.timeout, opener=self.opener)
            retrieved = _utc_string(_parse_now(self.utc_clock()))
            self._cache[limit] = (now, url, payload, retrieved)
            cache_state = "miss"
        observations = _validate_payload(payload, limit)
        current_time = _parse_now(self.utc_clock())
        latest_freshness = freshness_status(observations[0]["observed_at_utc"], now_utc=current_time)
        return {
            "provider": "alternative_me",
            "upstream_origin": "alternative_me_composite_fear_greed_index",
            "endpoint": "GET /fng/",
            "source_url": url,
            "retrieved_at_utc": retrieved,
            "cache": cache_state,
            "latest_observation_freshness": latest_freshness,
            "attribution_required": True,
            "attribution_text": "Data from alternative.me",
            "interpretation": "Bitcoin/large-crypto market sentiment composite; not an asset-specific signal, primary event proof, or independent confirmation of market metrics used in its construction.",
            "observations": observations,
            "raw_response": payload,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=7, help=f"daily observations, 1..{MAX_LIMIT} (default 7)")
    args = parser.parse_args(argv)
    try:
        result = AlternativeFNGClient().history(args.limit)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "alternative_me", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
