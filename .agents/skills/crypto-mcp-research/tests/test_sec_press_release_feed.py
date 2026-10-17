import datetime as dt
import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/sec_press_release_feed.py"
FIXTURE = pathlib.Path(__file__).parent / "fixtures/sec_press_releases.xml"
SPEC = importlib.util.spec_from_file_location("sec_press_release_feed", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

Client = module.SECPressReleaseFeedClient
ProviderError = module.ProviderError
UA = "Fin-Dex Research analyst@example.org"
FEED = FIXTURE.read_bytes()


class Headers(dict):
    pass


class Response:
    status = 200

    def __init__(self, body=FEED, *, url=module.FEED_URL, content_type="application/rss+xml; charset=utf-8"):
        self.body, self.url = body, url
        self.headers = Headers({"Content-Type": content_type})

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.body
    def geturl(self): return self.url


def test_missing_or_placeholder_user_agent_fails_before_network():
    for ua in (None, "", "Sample Company Name analyst@example.org", "Fin-Dex analyst", "Fin-Dex\nanalyst@example.org"):
        client = Client(user_agent=ua, opener=lambda *a, **k: pytest.fail("network must not run"))
        with pytest.raises(ProviderError):
            client.announcements()


def test_fixed_sec_rss_identity_and_timestamp_normalization():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return Response()

    fixed_now = "2026-10-17T16:00:00Z"
    result = Client(user_agent=UA, opener=opener, utc_clock=lambda: fixed_now).announcements(1)
    request, timeout = calls[0]
    assert request.full_url == module.FEED_URL
    assert request.method == "GET"
    assert request.get_header("User-agent") == UA
    assert request.get_header("Authorization") is None
    assert timeout == 10
    assert result["provider"] == "sec_press_release_rss"
    assert result["retrieved_at_utc"] == fixed_now
    assert result["scope"].startswith("SEC press releases")
    item = result["observations"][0]
    assert item["url"].startswith("https://www.sec.gov/newsroom/press-releases/")
    assert item["published_at_utc"] == "2026-10-16T14:30:00Z"
    assert item["event_time_utc"] is None
    assert result["response_sha256"]


@pytest.mark.parametrize("limit", [0, -1, 26, True])
def test_invalid_limit_does_not_request(limit):
    client = Client(user_agent=UA, opener=lambda *a, **k: pytest.fail("network must not run"))
    with pytest.raises(ValueError):
        client.announcements(limit)


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (402, "PAYMENT_REQUIRED"), (403, "FORBIDDEN"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED"),
])
def test_http_denials_stop_without_retry(code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as err:
        module._request(user_agent=UA, opener=opener)
    assert err.value.status == status
    assert len(calls) == 1


def test_timeout_stops_without_retry():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        module._request(user_agent=UA, opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [
    b"not xml", b"<rss><channel/></rss>",
    b"<!DOCTYPE rss [<!ENTITY x 'bad'>]><rss><channel><item><title>&x;</title></item></channel></rss>",
    b"<rss><channel><item><title>missing link</title></item></channel></rss>",
    b"<rss><channel><item><title>wrong URL</title><link>https://evil.example/x</link></item></channel></rss>",
])
def test_invalid_feed_data_rejected(body):
    with pytest.raises(ProviderError):
        module._parse_feed(body, limit=10)


def test_response_constraints_cache_and_safe_redirect():
    with pytest.raises(ProviderError, match="UNSAFE_REDIRECT"):
        module._request(user_agent=UA, opener=lambda req, timeout: Response(url="https://example.com/rss"))
    with pytest.raises(ProviderError, match="Content-Type"):
        module._request(user_agent=UA, opener=lambda req, timeout: Response(content_type="text/html"))
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        module._request(user_agent=UA, max_response_bytes=5, opener=lambda req, timeout: Response(body=b"123456"))

    calls, tick = [], [0.0]

    def opener(request, timeout):
        calls.append(request)
        return Response()

    client = Client(user_agent=UA, opener=opener, clock=lambda: tick[0], cache_ttl_seconds=900,
                    utc_clock=lambda: "2026-10-17T16:00:00Z")
    assert client.announcements()["cache"] == "miss"
    assert client.announcements(1)["cache"] == "hit"
    tick[0] = 901
    assert client.announcements()["cache"] == "miss"
    assert len(calls) == 2


def test_route_contract_records_parser_but_no_live_qualification():
    route = json.loads((ROOT / ".codex/sec-press-release-routing.json").read_text(encoding="utf-8"))
    assert route["access"]["runtime_readiness"] == "DOCUMENTED_UNQUALIFIED"
    assert route["route"]["parser"] == ".agents/skills/crypto-mcp-research/scripts/sec_press_release_feed.py"
    assert route["access"]["automated_user_agent"].find("no configured contact") >= 0
