"""Bounded SEC press-release RSS reader; requires a truthful declared User-Agent."""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable

FEED_URL = "https://www.sec.gov/rss/news/press.xml"
ALLOWED_HOST = "www.sec.gov"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 10
MAX_ITEMS = 25
UA_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


class SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in (None, 443):
            raise ProviderError("UNSAFE_REDIRECT", "SEC feed redirect left the fixed HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_user_agent(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("USER_AGENT_REQUIRED", "provide a truthful declared SEC User-Agent with organization name and contact email")
    value = value.strip()
    if len(value) > 200 or "\r" in value or "\n" in value:
        raise ProviderError("INVALID_USER_AGENT", "SEC User-Agent must be a single line of at most 200 characters")
    parts = value.rsplit(None, 1)
    if len(parts) != 2 or len(parts[0]) < 2 or not UA_EMAIL.fullmatch(parts[1]):
        raise ProviderError("INVALID_USER_AGENT", "SEC User-Agent must end with a contact email and include an organization name")
    if parts[0].lower() in {"example", "example company", "sample company name", "your organization"}:
        raise ProviderError("INVALID_USER_AGENT", "placeholder organization names are not accepted")
    return value


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _published(raw: str | None) -> tuple[str | None, str | None]:
    if not raw or not raw.strip():
        return None, raw
    raw = raw.strip()
    try:
        value = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None, raw
    if value.tzinfo is None:
        return None, raw
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), raw


def _child_text(node: ET.Element, name: str) -> str | None:
    child = next((item for item in node if item.tag.rsplit("}", 1)[-1].lower() == name.lower()), None)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _parse_feed(raw: bytes, *, limit: int) -> list[dict[str, Any]]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ProviderError("MALFORMED_DATA", "DTD and entity declarations are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ProviderError("MALFORMED_DATA", "response was not well-formed XML") from None
    if root.tag.rsplit("}", 1)[-1].lower() != "rss":
        raise ProviderError("MALFORMED_DATA", "expected RSS XML")
    channel = next((node for node in root if node.tag.rsplit("}", 1)[-1].lower() == "channel"), None)
    if channel is None:
        raise ProviderError("MALFORMED_DATA", "RSS channel is missing")
    items = [node for node in channel if node.tag.rsplit("}", 1)[-1].lower() == "item"]
    if not items:
        raise ProviderError("MALFORMED_DATA", "SEC RSS feed has no items")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items[:limit]:
        title, link = _child_text(item, "title"), _child_text(item, "link")
        if not title or not link:
            raise ProviderError("MALFORMED_DATA", "every SEC RSS item requires a title and link")
        parsed = urllib.parse.urlsplit(link)
        if parsed.scheme != "https" or parsed.hostname not in {"sec.gov", ALLOWED_HOST} or parsed.port not in (None, 443):
            raise ProviderError("IDENTITY_MISMATCH", "RSS item link is not an official SEC HTTPS URL")
        if link in seen:
            raise ProviderError("MALFORMED_DATA", "SEC RSS item URLs must be unique")
        published, published_raw = _published(_child_text(item, "pubDate"))
        output.append({
            "publisher": "U.S. Securities and Exchange Commission",
            "source_type": "primary_regulator_announcement",
            "title": title,
            "url": link,
            "guid": _child_text(item, "guid"),
            "published_at_utc": published,
            "published_at_raw": published_raw,
            "event_time_utc": None,
            "timestamp_note": "RSS publication time is SEC publisher metadata, not necessarily the event time.",
        })
        seen.add(link)
    return output


def _request(*, user_agent: str | None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] | None = None) -> tuple[bytes, str, str]:
    declared = _validate_user_agent(user_agent)
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    request = urllib.request.Request(FEED_URL, headers={
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
        "User-Agent": declared,
        "Accept-Encoding": "gzip, deflate",
    }, method="GET")
    if opener is None:
        opener = urllib.request.build_opener(SameHostRedirectHandler()).open
    try:
        with opener(request, timeout=timeout) as response:
            code = getattr(response, "status", 200)
            if not 200 <= code < 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {code}")
            final_url = response.geturl() if hasattr(response, "geturl") else FEED_URL
            final = urllib.parse.urlsplit(final_url)
            if final.scheme != "https" or final.hostname != ALLOWED_HOST or final.port not in (None, 443):
                raise ProviderError("UNSAFE_REDIRECT", "SEC feed response left the fixed HTTPS origin")
            raw = response.read(max_response_bytes + 1)
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
            raise ProviderError("TIMEOUT", "SEC feed request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    if "xml" not in content_type.lower():
        raise ProviderError("MALFORMED_DATA", "response Content-Type was not XML")
    return raw, final_url, content_type


@dataclass
class SECPressReleaseFeedClient:
    user_agent: str | None
    opener: Callable[..., Any] | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 900
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be between 0 and 3600 seconds")
        self._cache: tuple[float, bytes, str, str, str] | None = None

    def announcements(self, limit: int = 10) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_ITEMS:
            raise ValueError(f"limit must be between 1 and {MAX_ITEMS}")
        declared = _validate_user_agent(self.user_agent)
        now = self.clock()
        if self.cache_ttl_seconds and self._cache and now - self._cache[0] < self.cache_ttl_seconds:
            raw, source_url, content_type, retrieved = self._cache[1:]
            cache = "hit"
        else:
            raw, source_url, content_type = _request(user_agent=declared, timeout=self.timeout, opener=self.opener)
            retrieved = self.utc_clock()
            self._cache = (now, raw, source_url, content_type, retrieved)
            cache = "miss"
        observations = _parse_feed(raw, limit=limit)
        return {
            "provider": "sec_press_release_rss",
            "upstream_origin": "us_sec_official_press_release_feed",
            "endpoint": "GET /rss/news/press.xml",
            "source_url": source_url,
            "retrieved_at_utc": retrieved,
            "content_type": content_type,
            "cache": cache,
            "scope": "SEC press releases and regulator statements only; not issuer statements or the full market catalyst set.",
            "observations": observations,
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        result = SECPressReleaseFeedClient(user_agent=os.environ.get("SEC_USER_AGENT")).announcements(args.limit)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "sec_press_release_rss", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
