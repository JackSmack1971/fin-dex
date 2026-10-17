"""Bounded, anonymous, read-only Ethereum Foundation RSS announcement client."""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable

FEED_URL = "https://blog.ethereum.org/feed.xml"
ALLOWED_HOST = "blog.ethereum.org"
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 10
MAX_ITEMS = 25


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


class SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit only HTTPS redirects that remain on the official blog host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in (None, 443):
            raise ProviderError("UNSAFE_REDIRECT", "feed redirect left the fixed HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _utc_string(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _publication_time(raw: str | None) -> tuple[str | None, str | None]:
    if not raw or not raw.strip():
        return None, raw
    raw = raw.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None, raw
    if parsed.tzinfo is None:
        return None, raw
    return _utc_string(parsed), raw


def _item_text(item: ET.Element, name: str) -> str | None:
    node = next((child for child in item if child.tag.rsplit("}", 1)[-1] == name), None)
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None


def _parse_feed(raw: bytes, *, max_items: int) -> list[dict[str, Any]]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ProviderError("MALFORMED_DATA", "DTD and entity declarations are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ProviderError("MALFORMED_DATA", "response was not well-formed XML") from None
    if root.tag.rsplit("}", 1)[-1].lower() != "rss":
        raise ProviderError("MALFORMED_DATA", "expected an RSS 2.0 document")
    channel = next((child for child in root if child.tag.rsplit("}", 1)[-1] == "channel"), None)
    if channel is None:
        raise ProviderError("MALFORMED_DATA", "RSS channel is missing")
    items = [child for child in channel if child.tag.rsplit("}", 1)[-1] == "item"]
    if not items:
        raise ProviderError("MALFORMED_DATA", "RSS feed has no items")
    observations: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    for item in items[:max_items]:
        title = _item_text(item, "title")
        link = _item_text(item, "link")
        if not title or not link:
            raise ProviderError("MALFORMED_DATA", "each RSS item requires a title and link")
        parsed_link = urllib.parse.urlsplit(link)
        if parsed_link.scheme != "https" or parsed_link.hostname != ALLOWED_HOST or parsed_link.port not in (None, 443):
            raise ProviderError("IDENTITY_MISMATCH", "RSS item link is outside the official Ethereum Foundation blog")
        if link in seen_links:
            raise ProviderError("MALFORMED_DATA", "RSS item links must be unique")
        published_at, published_raw = _publication_time(_item_text(item, "pubDate"))
        observations.append({
            "publisher": "Ethereum Foundation",
            "source_type": "primary_source_announcement",
            "title": title,
            "url": link,
            "guid": _item_text(item, "guid"),
            "published_at_utc": published_at,
            "published_at_raw": published_raw,
            "event_time_utc": None,
            "timestamp_note": "RSS publication time is publisher metadata, not necessarily the event time.",
        })
        seen_links.add(link)
    return observations


def _request(*, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] | None = None) -> tuple[bytes, str, str]:
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    request = urllib.request.Request(
        FEED_URL,
        headers={"Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8", "User-Agent": USER_AGENT},
        method="GET",
    )
    if opener is None:
        opener = urllib.request.build_opener(SameHostRedirectHandler()).open
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if not 200 <= status_code < 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            final_url = response.geturl() if hasattr(response, "geturl") else FEED_URL
            final = urllib.parse.urlsplit(final_url)
            if final.scheme != "https" or final.hostname != ALLOWED_HOST or final.port not in (None, 443):
                raise ProviderError("UNSAFE_REDIRECT", "feed response left the fixed HTTPS origin")
            raw = response.read(max_response_bytes + 1)
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 402: "PAYMENT_REQUIRED", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
            raise ProviderError("TIMEOUT", "provider request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    if "xml" not in content_type.lower():
        raise ProviderError("MALFORMED_DATA", "response Content-Type was not XML")
    return raw, final_url, content_type


def _freshness(observed_at_utc: str | None, *, now: dt.datetime, max_age_seconds: int) -> str:
    if not observed_at_utc or now.tzinfo is None:
        return "UNVERIFIED"
    try:
        observed = dt.datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00"))
    except ValueError:
        return "UNVERIFIED"
    age = (now - observed).total_seconds()
    if observed.tzinfo is None or age < 0:
        return "UNVERIFIED"
    return "FRESH" if age <= max_age_seconds else "STALE"


@dataclass
class EthereumFoundationFeedClient:
    opener: Callable[..., Any] | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 900
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], dt.datetime] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be between 0 and 3600 seconds")
        self._cache: tuple[float, bytes, str, str, str] | None = None

    def announcements(self, limit: int = 10, *, max_age_seconds: int = 7 * 86_400) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_ITEMS:
            raise ValueError(f"limit must be between 1 and {MAX_ITEMS}")
        if isinstance(max_age_seconds, bool) or max_age_seconds < 0:
            raise ValueError("max_age_seconds must be nonnegative")
        now_mono = self.clock()
        if self.cache_ttl_seconds and self._cache and now_mono - self._cache[0] <= self.cache_ttl_seconds:
            raw, final_url, content_type, retrieved, cache_state = self._cache[1], self._cache[2], self._cache[3], self._cache[4], "hit"
        else:
            raw, final_url, content_type = _request(timeout=self.timeout, opener=self.opener)
            retrieved = _utc_string(self.utc_clock())
            self._cache = (now_mono, raw, final_url, content_type, retrieved)
            cache_state = "miss"
        observations = _parse_feed(raw, max_items=limit)
        latest = next((item["published_at_utc"] for item in observations if item["published_at_utc"]), None)
        return {
            "provider": "ethereum_foundation_blog_feed",
            "upstream_origin": "ethereum_foundation_official_blog",
            "endpoint": "GET /feed.xml",
            "source_url": final_url,
            "retrieved_at_utc": retrieved,
            "content_type": content_type,
            "cache": cache_state,
            "latest_observation_freshness": _freshness(latest, now=self.utc_clock(), max_age_seconds=max_age_seconds),
            "scope": "Ethereum Foundation announcements and blog posts only; not comprehensive Ethereum ecosystem or crypto-wide event coverage.",
            "observations": observations,
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help=f"items to return, 1..{MAX_ITEMS}")
    parser.add_argument("--max-age-seconds", type=int, default=7 * 86_400)
    args = parser.parse_args(argv)
    try:
        result = EthereumFoundationFeedClient().announcements(args.limit, max_age_seconds=args.max_age_seconds)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "ethereum_foundation_blog_feed", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
