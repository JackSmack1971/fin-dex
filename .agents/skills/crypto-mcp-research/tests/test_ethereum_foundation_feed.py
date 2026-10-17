import datetime as dt
import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/ethereum_foundation_feed.py"
FIXTURE = pathlib.Path(__file__).parent / "fixtures/ethereum_foundation_feed.xml"
SPEC = importlib.util.spec_from_file_location("ethereum_foundation_feed", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

EthereumFoundationFeedClient = module.EthereumFoundationFeedClient
ProviderError = module.ProviderError
_parse_feed = module._parse_feed
_request = module._request


class FakeHeaders(dict):
    pass


class FakeResponse:
    status = 200

    def __init__(self, body, *, url=module.FEED_URL, content_type="application/rss+xml"):
        self.body = body
        self.url = url
        self.headers = FakeHeaders({"Content-Type": content_type})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body

    def geturl(self):
        return self.url


FEED = FIXTURE.read_bytes()


def test_fixed_public_feed_identity_and_timestamp_normalization():
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return FakeResponse(FEED)

    fixed_now = dt.datetime(2026, 10, 17, 14, tzinfo=dt.timezone.utc)
    result = EthereumFoundationFeedClient(opener=opener, utc_clock=lambda: fixed_now).announcements(2)
    assert seen[0][0].full_url == "https://blog.ethereum.org/feed.xml"
    assert seen[0][0].method == "GET"
    assert seen[0][1] == 10
    assert result["endpoint"] == "GET /feed.xml"
    assert result["upstream_origin"] == "ethereum_foundation_official_blog"
    assert result["retrieved_at_utc"] == "2026-10-17T14:00:00Z"
    assert result["latest_observation_freshness"] == "FRESH"
    assert result["observations"][0]["published_at_utc"] == "2026-10-16T12:00:00Z"
    assert result["observations"][0]["event_time_utc"] is None
    assert result["response_sha256"]


def test_stale_and_missing_publication_times_remain_distinct():
    stale = _parse_feed(FEED, max_items=2)[1]["published_at_utc"]
    assert module._freshness(stale, now=dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc), max_age_seconds=86_400) == "STALE"
    assert module._freshness(None, now=dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc), max_age_seconds=86_400) == "UNVERIFIED"


@pytest.mark.parametrize("limit", [0, -1, 26, True])
def test_invalid_limit_rejected_without_network(limit):
    client = EthereumFoundationFeedClient(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.announcements(limit)


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (402, "PAYMENT_REQUIRED"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED")
])
def test_auth_payment_and_rate_limit_errors_stop_without_retry(code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as error:
        _request(opener=opener)
    assert error.value.status == status
    assert len(calls) == 1


def test_timeout_is_classified_without_retry():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        _request(opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [
    b"not xml",
    b"<feed><channel/></feed>",
    b"<!DOCTYPE rss [<!ENTITY x 'boom'>]><rss><channel><item><title>&x;</title></item></channel></rss>",
    b"<rss><channel><item><title>missing link</title></item></channel></rss>",
    b"<rss><channel><item><title>bad link</title><link>https://example.org/fake</link></item></channel></rss>",
])
def test_malformed_or_wrong_identity_feed_rejected(body):
    with pytest.raises(ProviderError):
        _parse_feed(body, max_items=10)


def test_unsafe_final_redirect_and_non_xml_content_are_rejected():
    with pytest.raises(ProviderError, match="UNSAFE_REDIRECT"):
        _request(opener=lambda request, timeout: FakeResponse(FEED, url="https://example.org/feed.xml"))
    with pytest.raises(ProviderError, match="Content-Type"):
        _request(opener=lambda request, timeout: FakeResponse(FEED, content_type="text/html"))


def test_response_size_limit_and_cache_are_enforced():
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        _request(max_response_bytes=2, opener=lambda request, timeout: FakeResponse(b"123"))
    calls = []
    tick = [0.0]

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse(FEED)

    client = EthereumFoundationFeedClient(opener=opener, clock=lambda: tick[0], cache_ttl_seconds=10)
    assert client.announcements(1)["cache"] == "miss"
    assert client.announcements(2)["cache"] == "hit"
    tick[0] = 11
    assert client.announcements(1)["cache"] == "miss"
    assert len(calls) == 2


def test_saved_live_smoke_binds_official_feed_response():
    policy = json.loads((ROOT / ".codex/ethereum-foundation-feed-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8"))
    assert policy["access"]["runtime_readiness"] == "RESPONDED"
    assert smoke["endpoint"] == "GET /feed.xml"
    assert smoke["source_url"] == "https://blog.ethereum.org/en/feed.xml"
    assert smoke["provider"] == "ethereum_foundation_blog_feed"
    assert smoke["retrieved_at_utc"] == "2026-10-17T14:28:26Z"
    assert len(smoke["observations"]) == 10
    assert smoke["observations"][0]["title"] == "Glamsterdam Hoodi Testnet Announcement"
    assert smoke["observations"][0]["published_at_utc"] == "2026-10-16T00:00:00Z"
    assert smoke["observations"][0]["event_time_utc"] is None
    assert smoke["response_sha256"] == policy["access"]["smoke_evidence"]["response_sha256"]
