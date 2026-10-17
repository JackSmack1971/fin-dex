"""Read-only single-contract Sourcify verification metadata lookup."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

BASE_URL = "https://sourcify.dev/server"
ALLOWED_HOST = "sourcify.dev"
USER_AGENT = "fin-dex-read-only-research/1.0 (+https://github.com/JackSmack1971/fin-dex)"
MAX_RESPONSE_BYTES = 16_384
DEFAULT_TIMEOUT_SECONDS = 10
POSITIVE_CACHE_SECONDS = None  # Sourcify asks callers to reuse stored verified data.
NEGATIVE_CACHE_SECONDS = 3600
CHAIN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
MATCH_VALUES = {"exact_match", "match", None}
CACHE_SCHEMA_VERSION = 1


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


class SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit only HTTPS redirects that remain on Sourcify's fixed API host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port not in (None, 443):
            raise ProviderError("UNSAFE_REDIRECT", "Sourcify redirect left the fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("MALFORMED_DATA", "verifiedAt must be an ISO-8601 timestamp or null")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderError("MALFORMED_DATA", "verifiedAt was not a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ProviderError("MALFORMED_DATA", "verifiedAt must include a timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_identity(chain_id: str, address: str) -> None:
    if not isinstance(chain_id, str) or not CHAIN_ID.fullmatch(chain_id):
        raise ValueError("chain_id must be a canonical positive decimal EVM chain ID")
    if not isinstance(address, str) or not EVM_ADDRESS.fullmatch(address):
        raise ValueError("address must be a 20-byte EVM contract address")


def _parse_response(payload: Any, chain_id: str, address: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProviderError("MALFORMED_DATA", "contract lookup response must be a JSON object")
    returned_chain = payload.get("chainId")
    returned_address = payload.get("address")
    if not isinstance(returned_chain, str) or not isinstance(returned_address, str):
        raise ProviderError("MALFORMED_DATA", "response is missing chain ID or contract address")
    if returned_chain != chain_id or returned_address.lower() != address.lower():
        raise ProviderError("IDENTITY_MISMATCH", "returned chain ID or contract address does not match the request")
    for field in ("match", "creationMatch", "runtimeMatch"):
        if field not in payload or payload[field] not in MATCH_VALUES:
            raise ProviderError("MALFORMED_DATA", f"{field} must be exact_match, match, or null")
    match_id = payload.get("matchId")
    if match_id is not None and not isinstance(match_id, (str, int)):
        raise ProviderError("MALFORMED_DATA", "matchId must be a string, integer, or null")
    return {
        "match": payload["match"],
        "creation_match": payload["creationMatch"],
        "runtime_match": payload["runtimeMatch"],
        "chain_id": returned_chain,
        "address": returned_address,
        "verified_at_utc": _parse_time(payload.get("verifiedAt")),
        "match_id": str(match_id) if match_id is not None else None,
    }


def _request(chain_id: str, address: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_response_bytes: int = MAX_RESPONSE_BYTES,
             opener: Callable[..., Any] | None = None) -> tuple[dict[str, Any], str, str]:
    _validate_identity(chain_id, address)
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("timeout and response limit must be positive")
    path = f"/v2/contract/{urllib.parse.quote(chain_id, safe='')}/{urllib.parse.quote(address, safe='')}"
    url = BASE_URL + path
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    if opener is None:
        opener = urllib.request.build_opener(SameHostRedirectHandler()).open
    try:
        with opener(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if not 200 <= status_code < 300:
                raise ProviderError("HTTP_ERROR", f"unexpected HTTP status {status_code}")
            final_url = response.geturl() if hasattr(response, "geturl") else url
            final = urllib.parse.urlsplit(final_url)
            if final.scheme != "https" or final.hostname != ALLOWED_HOST or final.port not in (None, 443):
                raise ProviderError("UNSAFE_REDIRECT", "Sourcify response left the fixed HTTPS host")
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        kind = {400: "INVALID_REQUEST", 401: "UNAUTHORIZED", 402: "PAYMENT_REQUIRED",
                403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProviderError(kind, f"Sourcify returned HTTP {exc.code}; stop this provider branch") from None
    except ProviderError:
        raise
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
            raise ProviderError("TIMEOUT", "Sourcify request timed out") from None
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    except OSError as exc:
        raise ProviderError("NETWORK_ERROR", type(exc).__name__) from None
    if len(raw) > max_response_bytes:
        raise ProviderError("RESPONSE_TOO_LARGE", f"response exceeded {max_response_bytes} bytes")
    if "json" not in content_type.lower():
        raise ProviderError("MALFORMED_DATA", "Sourcify response Content-Type was not JSON")
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ProviderError("MALFORMED_DATA", "Sourcify response was not valid UTF-8 JSON") from None
    return _parse_response(payload, chain_id, address), final_url, hashlib.sha256(raw).hexdigest()


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "fin-dex" / "sourcify-contract-cache-v1"


@dataclass
class SourcifyClient:
    opener: Callable[..., Any] | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cache_dir: Path | None = None
    clock: Callable[[], float] = time.time
    utc_clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.timeout > 30:
            raise ValueError("timeout must be within (0, 30] seconds")
        self.cache_dir = Path(self.cache_dir) if self.cache_dir else _default_cache_dir()

    def _cache_path(self, chain_id: str, address: str) -> Path:
        digest = hashlib.sha256(f"{chain_id}:{address.lower()}".encode("ascii")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path, chain_id: str, address: str) -> tuple[dict[str, Any], str] | None:
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if (entry.get("schema_version") != CACHE_SCHEMA_VERSION
                    or entry.get("chain_id") != chain_id
                    or not isinstance(entry.get("address"), str)
                    or entry["address"].lower() != address.lower()
                    or not isinstance(entry.get("retrieved_at_utc"), str)
                    or not isinstance(entry.get("response"), dict)):
                raise ValueError("cache entry identity/schema mismatch")
            response = _parse_response(entry["response"], chain_id, address)
            fetched = dt.datetime.fromisoformat(entry["retrieved_at_utc"].replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                raise ValueError("cache retrieval timestamp has no timezone")
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError, ProviderError):
            raise ProviderError("CACHE_CORRUPT", "stored Sourcify cache entry is invalid; no refetch was attempted") from None
        age = max(0.0, self.clock() - fetched.timestamp())
        is_positive = response["match"] in {"match", "exact_match"}
        if is_positive or age <= NEGATIVE_CACHE_SECONDS:
            return response, entry["retrieved_at_utc"]
        return None

    def contract(self, chain_id: str, address: str) -> dict[str, Any]:
        _validate_identity(chain_id, address)
        cache_path = self._cache_path(chain_id, address)
        cached = self._read_cache(cache_path, chain_id, address)
        if cached is not None:
            response, retrieved_at = cached
            cache_status = "persistent_hit"
            source_url = f"{BASE_URL}/v2/contract/{chain_id}/{address}"
            digest = None
        else:
            response, source_url, digest = _request(
                chain_id, address, timeout=self.timeout, opener=self.opener,
            )
            retrieved_at = self.utc_clock()
            entry = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "chain_id": chain_id,
                "address": address,
                "retrieved_at_utc": retrieved_at,
                "response": {
                    "match": response["match"],
                    "creationMatch": response["creation_match"],
                    "runtimeMatch": response["runtime_match"],
                    "chainId": response["chain_id"],
                    "address": response["address"],
                    "verifiedAt": response["verified_at_utc"],
                    "matchId": response["match_id"],
                },
            }
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_suffix(f".{os.getpid()}.tmp")
            try:
                tmp_path.write_text(json.dumps(entry, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
                os.replace(tmp_path, cache_path)
            except OSError:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ProviderError("CACHE_WRITE_FAILED", "Sourcify response received but could not be stored; do not refetch") from None
            cache_status = "stored"
        return {
            "provider": "sourcify_contract_repository",
            "upstream_origin": "sourcify_verified_contract_database",
            "endpoint": "GET /v2/contract/{chainId}/{address}",
            "source_url": source_url,
            "chain_id": response["chain_id"],
            "address": response["address"],
            "retrieved_at_utc": retrieved_at,
            "provider_verified_at_utc": response["verified_at_utc"],
            "match": response["match"],
            "creation_match": response["creation_match"],
            "runtime_match": response["runtime_match"],
            "match_id": response["match_id"],
            "response_sha256": digest,
            "cache": cache_status,
            "cache_policy": "Positive verification metadata is persisted indefinitely per Sourcify fair-use guidance; no-match results are cached for one hour. Cache contains only minimal verification metadata, never source files.",
            "interpretation": "Source-verification metadata only. A Sourcify match is not a security audit, proof of correct intent, or evidence that a token is safe.",
            "independence": "Do not count as independent security corroboration of GoPlus or other risk-scoring providers.",
            "data": response,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-id", required=True, help="exact positive decimal EVM chain ID")
    parser.add_argument("--address", required=True, help="exact 20-byte EVM contract address")
    args = parser.parse_args(argv)
    try:
        result = SourcifyClient().contract(args.chain_id, args.address)
    except (ProviderError, ValueError) as exc:
        print(json.dumps({"provider": "sourcify_contract_repository", "status": getattr(exc, "status", "INVALID_INPUT"), "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
