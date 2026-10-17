import importlib.util
import json
import pathlib
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/crypto-mcp-research/scripts/github_nitro_releases.py"
SPEC = importlib.util.spec_from_file_location("github_nitro_releases", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

Client = module.GitHubNitroReleasesClient
ProviderError = module.ProviderError

RELEASE = {
    "id": 123,
    "tag_name": "v3.12.1",
    "name": "Arbitrum Nitro v3.12.1",
    "html_url": "https://github.com/OffchainLabs/nitro/releases/tag/v3.12.1",
    "body": "Release notes",
    "published_at": "2026-10-02T22:54:03Z",
    "created_at": "2026-10-02T22:50:00Z",
    "draft": False,
    "prerelease": False,
}
BODY = json.dumps([RELEASE]).encode()


class Headers(dict):
    pass


class Response:
    status = 200

    def __init__(self, body=BODY, *, url=module.API_URL, content_type="application/json; charset=utf-8"):
        self.body, self.url = body, url
        self.headers = Headers({"Content-Type": content_type})

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.body
    def geturl(self): return self.url


def test_fixed_anonymous_read_only_release_route_and_normalization():
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return Response()

    client = Client(opener=opener, utc_clock=lambda: "2026-10-17T16:00:00Z")
    result = client.releases()
    request = seen[0][0]
    assert request.full_url == module.API_URL
    assert request.method == "GET"
    assert request.get_header("Authorization") is None
    assert request.get_header("X-github-api-version") == "2026-03-10"
    assert seen[0][1] == 10
    assert result["provider"] == "github_public_repo_releases"
    assert result["retrieved_at_utc"] == "2026-10-17T16:00:00Z"
    assert result["observations"][0]["tag_name"] == "v3.12.1"
    assert result["observations"][0]["event_time_utc"] is None
    assert result["coverage_status"] == "FIRST_PAGE_ONLY"


def test_optimism_repository_uses_fixed_allowlist_and_exact_release_identity():
    repo = "ethereum-optimism/optimism"
    release = dict(RELEASE, html_url="https://github.com/ethereum-optimism/optimism/releases/tag/op-node-v1.19.9")
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response(json.dumps([release]).encode(),
                        url="https://api.github.com/repos/ethereum-optimism/optimism/releases?per_page=10&page=1")

    result = Client(opener=opener, repository=repo).releases()
    assert seen[0].full_url == "https://api.github.com/repos/ethereum-optimism/optimism/releases?per_page=10&page=1"
    assert result["repository"] == repo
    assert result["observations"][0]["url"] == release["html_url"]
    assert result["observations"][0]["publisher"] == repo


def test_base_repository_uses_fixed_allowlist_and_exact_release_identity():
    repo = "base/base"
    release = dict(RELEASE, html_url="https://github.com/base/base/releases/tag/v1.3.0")
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response(json.dumps([release]).encode(),
                        url="https://api.github.com/repos/base/base/releases?per_page=10&page=1")

    result = Client(opener=opener, repository=repo).releases()
    assert seen[0].full_url == "https://api.github.com/repos/base/base/releases?per_page=10&page=1"
    assert result["repository"] == repo
    assert result["observations"][0]["url"] == release["html_url"]
    assert result["observations"][0]["publisher"] == repo


def test_ethereum_geth_repository_uses_fixed_allowlist_and_exact_release_identity():
    repo = "ethereum/go-ethereum"
    release = dict(RELEASE, name="Geth release", html_url="https://github.com/ethereum/go-ethereum/releases/tag/v1.15.0")
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response(json.dumps([release]).encode(),
                        url="https://api.github.com/repos/ethereum/go-ethereum/releases?per_page=10&page=1")

    result = Client(opener=opener, repository=repo).releases()
    assert seen[0].full_url == "https://api.github.com/repos/ethereum/go-ethereum/releases?per_page=10&page=1"
    assert result["repository"] == repo
    assert result["observations"][0]["url"] == release["html_url"]
    assert result["observations"][0]["publisher"] == repo


def test_unapproved_repository_rejected_before_request():
    with pytest.raises(ValueError, match="fixed GitHub release allowlist"):
        Client(repository="some-owner/some-repo",
               opener=lambda *args, **kwargs: pytest.fail("network should not be called"))


@pytest.mark.parametrize("limit", [0, -1, 11, True])
def test_limit_rejected_before_request(limit):
    client = Client(opener=lambda *args, **kwargs: pytest.fail("network should not be called"))
    with pytest.raises(ValueError):
        client.releases(limit)


@pytest.mark.parametrize(("code", "status"), [
    (401, "UNAUTHORIZED"), (402, "PAYMENT_REQUIRED"), (403, "FORBIDDEN"),
    (404, "NOT_FOUND"), (429, "RATE_LIMITED"),
])
def test_access_errors_stop_without_retry(code, status):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "fixture", {}, None)

    with pytest.raises(ProviderError) as err:
        module._request(opener=opener)
    assert err.value.status == status
    assert len(calls) == 1


def test_timeout_and_http_5xx_are_one_attempt():
    calls = []

    def timeout(request, timeout):
        calls.append(request)
        raise TimeoutError("fixture")

    with pytest.raises(ProviderError, match="TIMEOUT"):
        module._request(opener=timeout)
    assert len(calls) == 1

    def server_error(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 503, "fixture", {}, None)

    with pytest.raises(ProviderError, match="HTTP_ERROR"):
        module._request(opener=server_error)
    assert len(calls) == 2


@pytest.mark.parametrize("raw", [b"not json", b"{}", b"[null]", b"[{}]"])
def test_malformed_release_responses_rejected(raw):
    with pytest.raises(ProviderError):
        module._parse(raw, limit=10)


def test_wrong_repository_duplicate_ids_oversize_and_non_json_rejected():
    wrong = dict(RELEASE, html_url="https://github.com/other/project/releases/tag/v1")
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        module._parse(json.dumps([wrong]).encode(), limit=10)
    wrong_path = dict(RELEASE, html_url="https://github.com/OffchainLabs/nitro/issues/1")
    with pytest.raises(ProviderError, match="IDENTITY_MISMATCH"):
        module._parse(json.dumps([wrong_path]).encode(), limit=10)
    with pytest.raises(ProviderError, match="unique integer id"):
        module._parse(json.dumps([RELEASE, RELEASE]).encode(), limit=10)
    with pytest.raises(ProviderError, match="fixed page size"):
        module._parse(json.dumps([RELEASE] * 11).encode(), limit=10)
    with pytest.raises(ProviderError, match="Content-Type"):
        module._request(opener=lambda request, timeout: Response(content_type="text/html"))
    with pytest.raises(ProviderError, match="RESPONSE_TOO_LARGE"):
        module._request(max_response_bytes=2, opener=lambda request, timeout: Response(body=b"123"))


def test_unsafe_redirect_cache_and_draft_exclusion():
    with pytest.raises(ProviderError, match="UNSAFE_REDIRECT"):
        module._request(opener=lambda request, timeout: Response(url="https://example.com/releases"))
    with pytest.raises(ProviderError, match="fixed repository release route"):
        module._request(repository="ethereum-optimism/optimism",
                        opener=lambda request, timeout: Response(url=module.API_URL.replace("OffchainLabs/nitro", "some-owner/repo")))
    draft = dict(RELEASE, id=456, draft=True)
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return Response(json.dumps([draft, RELEASE]).encode())

    tick = [0.0]
    client = Client(opener=opener, clock=lambda: tick[0], utc_clock=lambda: "2026-10-17T16:00:00Z")
    first = client.releases(2)
    second = client.releases(2)
    assert first["cache"] == "miss"
    assert second["cache"] == "hit"
    assert len(first["observations"]) == len(second["observations"]) == 1
    assert len(calls) == 1


def test_saved_smoke_matches_route_and_documented_observation():
    policy = json.loads((ROOT / ".codex/github-nitro-releases-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8"))
    assert policy["access"]["runtime_readiness"] == "RESPONDED"
    assert smoke["endpoint"] == "GET /repos/OffchainLabs/nitro/releases?per_page=10&page=1"
    assert smoke["repository"] == "OffchainLabs/nitro"
    assert smoke["observations"]
    assert smoke["observations"][0]["url"].startswith("https://github.com/OffchainLabs/nitro/releases/tag/")


def test_saved_optimism_smoke_matches_repository_route():
    policy = json.loads((ROOT / ".codex/github-optimism-releases-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8"))
    assert policy["repository"] == smoke["repository"] == "ethereum-optimism/optimism"
    assert policy["access"]["runtime_readiness"] == "RESPONDED"
    assert smoke["endpoint"] == "GET /repos/ethereum-optimism/optimism/releases?per_page=10&page=1"
    assert smoke["observations"][0]["url"].startswith("https://github.com/ethereum-optimism/optimism/releases/tag/")


def test_saved_base_smoke_matches_repository_route():
    policy = json.loads((ROOT / ".codex/github-base-releases-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / "research-runs/provider-smokes/github-base-releases-2026-10-17.json").read_text(encoding="utf-8"))
    assert policy["repository"] == smoke["repository"] == "base/base"
    assert policy["access"]["runtime_readiness"] == "RESPONDED"
    assert policy["access"]["smoke_evidence"]["artifact"] == "research-runs/provider-smokes/github-base-releases-2026-10-17.json"
    assert smoke["endpoint"] == "GET /repos/base/base/releases?per_page=10&page=1"
    assert smoke["retrieved_at_utc"] == policy["access"]["smoke_evidence"]["retrieved_at_utc"]
    assert len(smoke["observations"]) == 10
    assert smoke["observations"][0]["tag_name"] == "v1.5.0"
    assert smoke["observations"][0]["url"].startswith("https://github.com/base/base/releases/tag/")
    assert smoke["response_sha256"] == policy["access"]["smoke_evidence"]["response_sha256"]
    assert smoke["observations"][0]["event_time_utc"] is None


def test_saved_geth_smoke_matches_repository_route():
    policy = json.loads((ROOT / ".codex/github-ethereum-releases-routing.json").read_text(encoding="utf-8"))
    smoke = json.loads((ROOT / policy["access"]["smoke_evidence"]["artifact"]).read_text(encoding="utf-8"))
    assert policy["repository"] == smoke["repository"] == "ethereum/go-ethereum"
    assert policy["access"]["runtime_readiness"] == "RESPONDED"
    assert smoke["endpoint"] == "GET /repos/ethereum/go-ethereum/releases?per_page=10&page=1"
    assert smoke["observations"]
    assert smoke["observations"][0]["url"].startswith("https://github.com/ethereum/go-ethereum/releases/tag/")
    assert smoke["response_sha256"] == policy["access"]["smoke_evidence"]["response_sha256"]
    assert smoke["observations"][0]["event_time_utc"] is None
