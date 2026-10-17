"""Bounded anonymous GitHub public-release discovery for fixed repositories."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

API_URL = "https://api.github.com/repos/OffchainLabs/nitro/releases?per_page=10&page=1"
API_HOST = "api.github.com"
REPOSITORY = "OffchainLabs/nitro"
ALLOWED_REPOSITORIES = {REPOSITORY, "ethereum-optimism/optimism", "base/base", "ethereum/go-ethereum"}
USER_AGENT = "fin-dex-read-only-research/1.0"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RELEASES = 10


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


class SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != API_HOST or parsed.port not in (None, 443):
            raise ProviderError("UNSAFE_REDIRECT", "GitHub API redirect left the fixed HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _request(*, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] | None = None,
             repository: str = REPOSITORY) -> tuple[bytes, str, str]:
    if repository not in ALLOWED_REPOSITORIES:
        raise ValueError("repository is outside the fixed GitHub release allowlist")
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    api_url = f"https://{API_HOST}/repos/{repository}/releases?per_page=10&page=1"
    request = urllib.request.Request(api_url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": USER_AGENT,
    }, method="GET")
    if opener is None:
        opener = urllib.request.build_opener(SameHostRedirectHandler()).open
    try:
        with opener(request, timeout=timeout) as response:
            code = getattr(response, "status", 200)
            if not 200 <= code < 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {code}")
            final_url = response.geturl() if hasattr(response, "geturl") else API_URL
            parsed = urllib.parse.urlsplit(final_url)
            if parsed.scheme != "https" or parsed.hostname != API_HOST or parsed.port not in (None, 443):
                raise ProviderError("UNSAFE_REDIRECT", "GitHub API response left the fixed HTTPS origin")
            expected_path = f"/repos/{repository}/releases"
            expected_query = "per_page=10&page=1"
            if parsed.path.lower() != expected_path.lower() or parsed.query != expected_query:
                raise ProviderError("UNSAFE_REDIRECT", "GitHub API response left the fixed repository release route")
            raw = response.read(max_response_bytes + 1)
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
    except urllib.error.HTTPError as exc:
        kind = {401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED", 403: "FORBIDDEN",
                404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"HTTP {exc.code}; provider branch stopped") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
            raise ProviderError("TIMEOUT", "GitHub API request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    if "json" not in content_type.lower():
        raise ProviderError("MALFORMED_DATA", "response Content-Type was not JSON")
    return raw, final_url, content_type


def _parse(raw: bytes, *, limit: int, repository: str = REPOSITORY) -> list[dict[str, Any]]:
    if repository not in ALLOWED_REPOSITORIES:
        raise ValueError("repository is outside the fixed GitHub release allowlist")
    try:
        values = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "response was not valid UTF-8 JSON") from None
    if not isinstance(values, list):
        raise ProviderError("MALFORMED_DATA", "release response must be an array")
    if len(values) > MAX_RELEASES:
        raise ProviderError("MALFORMED_DATA", "response exceeded the fixed page size")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in values[:limit]:
        if not isinstance(item, dict):
            raise ProviderError("MALFORMED_DATA", "release entry must be an object")
        if item.get("draft") is True:
            continue
        url = item.get("html_url")
        tag = item.get("tag_name")
        rid = item.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool) or rid in seen or not isinstance(tag, str) or not tag:
            raise ProviderError("MALFORMED_DATA", "release requires a unique integer id and nonempty tag")
        parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        expected_path = ["", *repository.lower().split("/"), "releases", "tag"]
        if (parsed is None or parsed.scheme != "https" or parsed.hostname != "github.com"
                or parsed.path.lower().split("/")[:len(expected_path)] != expected_path):
            raise ProviderError("IDENTITY_MISMATCH", f"release URL does not identify a tagged release for {repository}")
        published = item.get("published_at")
        if published is not None and not isinstance(published, str):
            raise ProviderError("MALFORMED_DATA", "published_at must be an ISO timestamp or null")
        rows.append({
            "publisher": repository,
            "source_type": "primary_source_tagged_software_release",
            "release_id": rid,
            "tag_name": tag,
            "name": item.get("name"),
            "title": item.get("name") or tag,
            "url": url,
            "body": item.get("body"),
            "published_at_utc": published,
            "created_at_utc": item.get("created_at"),
            "event_time_utc": None,
            "timestamp_note": "GitHub published_at/created_at are release metadata, not necessarily the event time.",
            "prerelease": item.get("prerelease") is True,
        })
        seen.add(rid)
    return rows


@dataclass
class GitHubNitroReleasesClient:
    opener: Callable[..., Any] | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = 300
    clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], str] = _utc_now
    repository: str = REPOSITORY

    def __post_init__(self) -> None:
        if self.repository not in ALLOWED_REPOSITORIES:
            raise ValueError("repository is outside the fixed GitHub release allowlist")
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        if not 0 <= self.cache_ttl_seconds <= 3600:
            raise ValueError("cache TTL must be between 0 and 3600 seconds")
        self._cache: tuple[float, bytes, str, str, str] | None = None

    def releases(self, limit: int = MAX_RELEASES) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_RELEASES:
            raise ValueError(f"limit must be between 1 and {MAX_RELEASES}")
        now = self.clock()
        if self.cache_ttl_seconds and self._cache and now - self._cache[0] <= self.cache_ttl_seconds:
            raw, source_url, content_type, retrieved = self._cache[1:]
            cache = "hit"
        else:
            raw, source_url, content_type = _request(timeout=self.timeout, opener=self.opener,
                                                      repository=self.repository)
            retrieved = self.utc_clock()
            self._cache = (now, raw, source_url, content_type, retrieved)
            cache = "miss"
        observations = _parse(raw, limit=limit, repository=self.repository)
        return {
            "provider": "github_public_repo_releases",
            "upstream_origin": "github_repository_release_records",
            "repository": self.repository,
            "endpoint": f"GET /repos/{self.repository}/releases?per_page=10&page=1",
            "source_url": source_url,
            "retrieved_at_utc": retrieved,
            "content_type": content_type,
            "cache": cache,
            "coverage_status": "FIRST_PAGE_ONLY",
            "scope": f"Published tagged releases for {self.repository} only; release metadata is not event time and does not cover all ecosystem announcements or events.",
            "observations": observations,
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", choices=sorted(ALLOWED_REPOSITORIES), default=REPOSITORY)
    args = parser.parse_args()
    try:
        result = GitHubNitroReleasesClient(repository=args.repository).releases()
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "github_public_repo_releases", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
