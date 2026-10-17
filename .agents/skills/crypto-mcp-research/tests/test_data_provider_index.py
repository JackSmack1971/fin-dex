import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[4]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_canonical_provider_index_routes_all_defined_tasks():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    assert index["schema_version"] == 1
    assert "Only documented free, anonymous, read-only" in index["authorization"]["baseline"]
    routes = index["task_routes"]
    assert len(routes) == 8
    assert len({route["task"] for route in routes}) == len(routes)
    for route in routes:
        assert isinstance(route["source_priority"], list)
        assert isinstance(route["fallback"], str) and route["fallback"]
        assert route["required_evidence"]


def test_provider_route_contract_references_exist_and_names_match():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    for task in index["task_routes"]:
        for entry in task["source_priority"]:
            relative = entry.get("route_contract")
            if relative is None:
                continue
            contract = read_json(ROOT / ".codex" / relative)
            assert contract["provider"] == entry["provider"]


def test_index_keeps_unverified_fallbacks_and_derivatives_gaps_explicit():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    dex = next(route for route in index["task_routes"] if route["task"].startswith("Exact DEX"))
    derivatives = next(route for route in index["task_routes"] if route["task"].startswith("Historical derivatives"))
    assert "Implemented bounded DEX Screener fallback" in dex["fallback"]
    transfers = next(route for route in index["task_routes"] if route["task"] == "Transfer-level stablecoin activity")
    routescan = next(item for item in transfers["source_priority"] if item["provider"] == "routescan_keyless_erc20_transfers")
    assert routescan["route_contract"] is None
    assert routescan["smoke_evidence"] is None
    assert routescan["readiness"].startswith("DOCUMENTED_FREE_KEYLESS_READ_ONLY_ENDPOINT")
    assert "project's use scope is unresolved" in routescan["readiness"]
    assert "2 requests/second" in routescan["documented_public_limits"]
    assert "mint/burn" in routescan["coverage_caveats"]
    matrix = (ROOT / ".agents/skills/crypto-mcp-research/references/free-data-expansion.md").read_text(encoding="utf-8")
    table_lines = [line for line in matrix.splitlines() if line.startswith("|")]
    assert table_lines and all(line.count("|") == 8 for line in table_lines)
    assert "Routescan is recorded as a **candidate only**" in matrix
    assert "no call or client is permitted" in matrix
    assert "project use scope is unresolved" in matrix
    assert len(derivatives["source_priority"]) == 11
    assert any(item["provider"] == "binance_vision_um_metrics_archive" for item in derivatives["source_priority"])
    assert derivatives["source_priority"][0]["provider"] == "defillama_public"
    assert "not contract-specific" in derivatives["source_priority"][0]["readiness"]
    candidates = {candidate["provider"]: candidate for candidate in derivatives["source_priority"]}
    binance = candidates["binance_usdm_public"]
    assert binance["readiness"].startswith("IMPLEMENTED_FIXTURE_TESTED")
    assert "HTTP 451" in binance["readiness"]
    assert binance["route_contract"] == "binance-futures-routing.json"
    evidence = read_json(ROOT / binance["smoke_evidence"])
    assert evidence["response"]["http_status"] == 451
    bybit = candidates["bybit_v5_public_market"]
    assert bybit["readiness"].startswith("UNAVAILABLE")
    assert bybit["route_contract"] is None
    evidence = read_json(ROOT / bybit["smoke_evidence"])
    assert [request["http_status"] for request in evidence["requests"]] == [403, 403]
    assert all(request["authorization_header_sent"] is False for request in evidence["requests"])
    assert evidence["attempt_policy"]["retry"] is False
    assert evidence["attempt_policy"]["alternate_host_or_proxy"] is False
    hyperliquid = candidates["hyperliquid_public_info"]
    assert hyperliquid["route_contract"] == "hyperliquid-routing.json"
    assert hyperliquid["readiness"].startswith("RESPONDED for ETH only")
    assert "Historical OI is not provided" in hyperliquid["readiness"]
    assert hyperliquid["smoke_evidence"] == [
        "research-runs/provider-smokes/hyperliquid-eth-funding-24h.json",
        "research-runs/provider-smokes/hyperliquid-eth-perp-snapshot.json",
    ]
    dydx = candidates["dydx_public_indexer"]
    assert dydx["readiness"].startswith("REJECTED_FOR_THIS_ENVIRONMENT")
    assert "United States or Canada" in dydx["readiness"]
    assert dydx["route_contract"] is None
    assert "https://docs.dydx.xyz/indexer-client/http" in dydx["documentation"]
    assert "no endpoint request was made" in dydx["readiness"]
    fin_data = candidates["fin_data_mcp_http_v2_derivatives"]
    assert fin_data["readiness"].startswith("UNQUALIFIED_RUNTIME_TOOL_METADATA_ONLY")
    assert "No tool call made" in fin_data["readiness"]
    assert fin_data["route_contract"] is None
    assert set(fin_data["documentation"]) == {
        "https://app.okx.com/docs-v5/en/",
        "https://app.okx.com/docs-v5/agent_en/",
        "https://app.okx.com/docs-v5/log_en/",
    }
    okx = candidates["okx_public_rubik_oi_history"]
    assert okx["readiness"].startswith("UNQUALIFIED_DOCUMENTATION_GAP_AND_US_SCOPE")
    assert "No request made" in okx["readiness"]
    assert okx["route_contract"] is None
    assert "endpoint-specific rate limit" in okx["readiness"]
    assert "historical OI" in derivatives["fallback"]


def test_transfer_provider_denials_are_retained_without_claiming_log_access():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    route = next(item for item in index["task_routes"] if item["task"] == "Transfer-level stablecoin activity")
    candidates = {item["provider"]: item for item in route["source_priority"]}
    for provider, artifact_path in [
        ("solidrpc_public_ethereum", "research-runs/provider-smokes/solidrpc-keyless-ethereum-blocknumber-403.json"),
        ("drpc_public_ethereum", "research-runs/provider-smokes/drpc-public-ethereum-blocknumber-403.json"),
    ]:
        candidate = candidates[provider]
        assert candidate["route_contract"] is None
        assert candidate["smoke_evidence"] == artifact_path
        assert "HTTP 403" in candidate["readiness"]
        evidence = read_json(ROOT / artifact_path)
        assert evidence["http_status"] == 403
        assert evidence["authorization_header_sent"] is False
        assert evidence["request"]["method"] == "eth_blockNumber"
        assert "eth_getLogs was not reached" in evidence["observation"]
        assert evidence["retry_policy"].startswith("No retry")

    blast = next(item for item in route["source_priority"] if item["provider"] == "blastapi_public_ethereum_rpc")
    assert blast["route_contract"] is None
    assert blast["readiness"].startswith("REJECTED_DEPRECATED_SERVICE")
    assert "https://eth-mainnet.public.blastapi.io" == blast["catalog_endpoint"]
    assert "No qualified chain-wide transfer-history provider" in route["fallback"]


def test_coinpaprika_historical_route_is_scoped_by_free_plan_terms():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    history = next(route for route in index["task_routes"] if route["task"] == "Historical observations and deterministic replay")
    entry = history["source_priority"][0]
    contract = read_json(ROOT / ".codex/coinpaprika-history-routing.json")
    assert entry["provider"] == contract["provider"]
    assert entry["readiness"].startswith("RESPONDED_FOR_EXACT_ETHEREUM_USDC_DAILY_QUERY")
    assert "current project scope is not verified" in entry["readiness"]
    assert contract["access"]["plan_limits"]["redistribution"] == "not included in Free plan"
    assert "PERSONAL_NONCOMMERCIAL_ONLY" in contract["access"]["policy_state"]
    assert contract["route"]["automatic_retries"] == 0


def test_gdelt_rate_limit_is_retained_as_unavailable_not_qualified():
    contract = read_json(ROOT / ".codex/gdelt-doc-routing.json")
    smoke = read_json(ROOT / contract["access"]["last_smoke"]["artifact"])
    assert contract["access"]["runtime_readiness"] == "UNAVAILABLE"
    assert smoke["http_status"] == 429
    assert smoke["retrieved_at_utc"] == "2026-10-17T14:26:19Z"
    assert smoke["completed_at_utc"] == "2026-10-17T14:26:28Z"
    assert smoke["request"]["auth"] == "none"
    index = read_json(ROOT / ".codex/data-provider-index.json")
    catalyst = next(route for route in index["task_routes"] if route["task"].startswith("Catalyst"))
    proxy = next(item for item in catalyst["source_priority"] if item["provider"] == "gdelt_mcp_public_host")
    assert proxy["route_contract"] is None
    assert proxy["readiness"].startswith("HOSTED_ROUTE_DOCUMENTED_UNQUALIFIED")
    assert "privacy" in proxy["readiness"] and "retention" in proxy["readiness"]


def test_ethereum_foundation_public_feed_is_scoped_and_response_bound():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    catalyst = next(route for route in index["task_routes"] if route["task"].startswith("Catalyst"))
    entry = catalyst["source_priority"][0]
    contract = read_json(ROOT / ".codex/ethereum-foundation-feed-routing.json")
    smoke = read_json(ROOT / contract["access"]["smoke_evidence"]["artifact"])
    assert entry["provider"] == "ethereum_foundation_blog_feed"
    assert entry["route_contract"] == "ethereum-foundation-feed-routing.json"
    assert entry["readiness"].startswith("RESPONDED")
    assert contract["access"]["runtime_readiness"] == "RESPONDED"
    assert contract["access"]["auth"] == "none observed on bounded anonymous GET"
    assert smoke["provider"] == contract["provider"]
    assert smoke["source_url"] == "https://blog.ethereum.org/en/feed.xml"
    assert smoke["retrieved_at_utc"] == contract["access"]["smoke_evidence"]["retrieved_at_utc"]
    assert contract["evidence"]["source_type"] == "primary_source_for_Ethereum_Foundation_statements_only"


def test_stablecoin_flow_candidates_remain_unqualified_for_documented_reasons():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    flow = next(route for route in index["task_routes"] if route["task"] == "Transfer-level stablecoin activity")
    assert "No qualified chain-wide transfer-history provider" in flow["fallback"]
    assert flow["source_priority"][0]["readiness"].startswith("REJECTED_FOR_THIS_USE")
    assert flow["source_priority"][0]["route_contract"] is None
    assert flow["source_priority"][1]["readiness"].startswith("DOCUMENTED_FREE_TIER_REQUIRES_ACCOUNT_AND_API_KEY")
    assert "billing/authorization is unknown" in flow["source_priority"][1]["readiness"]
    assert "address-scoped" in flow["source_priority"][1]["readiness"]
    assert flow["source_priority"][1]["route_contract"] is None
    rpc = next(item for item in flow["source_priority"] if item["provider"] == "1rpc_public_ethereum")
    assert rpc["provider"] == "1rpc_public_ethereum"
    assert rpc["readiness"].startswith("UNAVAILABLE_FOR_ETH_GETLOGS")
    assert rpc["route_contract"] is None
    smoke = read_json(ROOT / rpc["smoke_evidence"])
    assert smoke["transport"]["http_status"] == 200
    assert smoke["transport"]["response"]["error"] == {"code": -32000, "message": "method not available"}
    assert smoke["authorization_header_sent"] is False


def test_ankr_public_rpc_is_disabled_after_anonymous_auth_denial():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    flow = next(route for route in index["task_routes"] if route["task"] == "Transfer-level stablecoin activity")
    ankr = next(item for item in flow["source_priority"] if item["provider"] == "ankr_public_ethereum_rpc")
    assert ankr["route_contract"] == "ankr-public-ethereum-routing.json"
    assert ankr["smoke_evidence"] == "research-runs/provider-smokes/ankr-ethereum-public-eth-blocknumber-401.json"
    assert ankr["readiness"].startswith("UNAVAILABLE_ANONYMOUS_ACCESS_REQUIRES_KEY")
    assert "eth_getLogs was not called" in ankr["readiness"]
    assert "1,000-block maximum range" in ankr["readiness"]
    assert "https://rpc.ankr.com/eth" in ankr["readiness"]
    contract = read_json(ROOT / ".codex/ankr-public-ethereum-routing.json")
    smoke = read_json(ROOT / ankr["smoke_evidence"])
    assert contract["route"]["allowed_methods"] == ["eth_chainId", "eth_getLogs"]
    assert contract["access"]["runtime_readiness"] == "UNAVAILABLE_ANONYMOUS_ACCESS_REQUIRES_KEY"
    assert smoke["transport"]["authorization_header_sent"] is False
    assert smoke["response"]["error"]["code"] == -32000
    assert "API key" in smoke["response"]["error"]["message"]
    assert smoke["attempt_policy"]["probe_invocations"] == 2
    assert smoke["attempt_policy"]["repeated_probe_occurred"] is True
    assert smoke["attempt_policy"]["followup_method_call"] is False
    assert "internal research only" in contract["access"]["terms_scope"]
    assert "does not include block timestamps" in contract["evidence"]["observation_timestamp"]


def test_sec_press_release_feed_is_primary_but_not_automated_without_user_agent():
    contract = read_json(ROOT / ".codex/sec-press-release-routing.json")
    assert contract["access"]["runtime_readiness"] == "DOCUMENTED_UNQUALIFIED"
    assert contract["access"]["registration_is_authorization"] is False
    assert "no automated request was made" in contract["access"]["automated_user_agent"]
    assert contract["route"]["parser"] == ".agents/skills/crypto-mcp-research/scripts/sec_press_release_feed.py"
    assert contract["route"]["parser_status"].startswith("IMPLEMENTED_FIXTURE_TESTED")
    assert contract["route"]["request_constraints"]["requires_environment_variable"] == "SEC_USER_AGENT"


def test_runtime_statuses_match_saved_smoke_responses():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    provider_files = {
        "defillama_public": ".codex/data-provider-routing.json",
        "dexscreener_public": ".codex/dexscreener-routing.json",
        "alternative_fng_public": ".codex/alternative-fng-routing.json",
    }
    for provider, route_file in provider_files.items():
        contract = read_json(ROOT / route_file)
        smoke = contract["access"].get("smoke_evidence")
        if smoke:
            artifact = read_json(ROOT / smoke["artifact"])
            assert artifact["provider"] == provider.split("_public")[0] or provider == "alternative_fng_public" and artifact["provider"] == "alternative_me"
            assert artifact["retrieved_at_utc"].endswith("Z")
        assert contract["access"]["runtime_readiness"] in {"QUALIFIED", "PARTIALLY_QUALIFIED", "IMPLEMENTED_UNSMOKED", "UNAVAILABLE", "RESPONDED"}


def test_dexpaprika_response_is_identity_matched_but_not_promoted_to_qualified():
    inventory = read_json(ROOT / ".codex/provider-capabilities.json")
    provider = next(item for item in inventory["providers"] if item["id"] == "dexpaprika")
    evidence = read_json(ROOT / "research-runs/provider-smokes/dexpaprika-ethereum-usdc-weth-pool.json")
    direct_smoke = read_json(ROOT / "research-runs/provider-smokes/dexpaprika-public-api-ethereum-usdc-weth.json")
    comparator = read_json(ROOT / "research-runs/provider-smokes/dexscreener-ethereum-usdc-weth.json")
    contract = read_json(ROOT / ".codex/dexpaprika-routing.json")
    assert provider["status"] == "RESPONDED"
    assert provider["responded_evidence"]["tool_identity"] == "mcp__dexpaprika__getPoolDetails"
    assert evidence["retrieved_at_utc"] == "2026-10-17T14:16:50Z"
    assert evidence["source_url"] == "https://api.dexpaprika.com/networks/ethereum/pools/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
    assert evidence["identity"]["pool_address"].lower() == comparator["requested_identifier"].lower()
    assert evidence["identity"]["base_token_id"].lower() == comparator["data"]["pair"]["baseToken"]["address"].lower()
    assert evidence["identity"]["quote_token_id"].lower() == comparator["data"]["pair"]["quoteToken"]["address"].lower()
    assert evidence["source_timestamps"]["price_time"] == "2026-10-17T14:16:35Z"
    assert evidence["comparison_context"]["dexscreener_metric_timestamp"] == "not supplied"
    assert direct_smoke["http_status"] == 200
    assert direct_smoke["authorization_header_sent"] is False
    assert direct_smoke["retrieved_at_utc"] == "2026-10-17T14:19:16Z"
    assert direct_smoke["data"]["id"].lower() == evidence["identity"]["pool_address"].lower()
    assert direct_smoke["data"]["liquidity_usd"] == evidence["data"]["liquidity_usd"]
    assert contract["access"]["anonymous_rest_smoke"]["authorization_header_sent"] is False
    assert contract["independence"]["status"] == "UNKNOWN"
    assert provider["qualification_evidence"] is None


def test_catalyst_sources_preserve_uniswap_rejection_and_github_release_route_limits():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    catalyst = next(route for route in index["task_routes"] if route["task"].startswith("Catalyst"))
    candidates = {item["provider"]: item for item in catalyst["source_priority"] if item["provider"] != "github_public_repo_releases"}
    uniswap = candidates["uniswap_governance_discourse"]
    assert uniswap["readiness"].startswith("REJECTED_FOR_AUTOMATION")
    assert uniswap["route_contract"] is None
    github_routes = [item for item in catalyst["source_priority"] if item["provider"] == "github_public_repo_releases"]
    assert {item["route_contract"] for item in github_routes} == {
        "github-nitro-releases-routing.json",
        "github-optimism-releases-routing.json",
        "github-base-releases-routing.json",
        "github-ethereum-releases-routing.json",
    }
    for github in github_routes:
        assert github["readiness"].startswith("RESPONDED")
        smoke = read_json(ROOT / github["smoke_evidence"])
        assert smoke["provider"] == "github_public_repo_releases"
        assert smoke["repository"] in {"OffchainLabs/nitro", "ethereum-optimism/optimism", "base/base", "ethereum/go-ethereum"}
        assert len(smoke["observations"]) == 10
        assert smoke["observations"][0]["event_time_utc"] is None
    atom = candidates["github_public_repo_release_atom"]
    assert atom["readiness"].startswith("HISTORICAL_DISCOVERY_ONLY")
    assert atom["route_contract"] is None
    atom_smoke = read_json(ROOT / atom["smoke_evidence"])
    assert atom_smoke["route_format_officially_documented"] is False


def test_circle_pressroom_remains_discovery_only_without_a_verified_feed_route():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    catalyst = next(route for route in index["task_routes"] if route["task"].startswith("Catalyst"))
    circle = next(item for item in catalyst["source_priority"] if item["provider"] == "circle_official_pressroom")
    assert circle["route_contract"] is None
    assert circle["readiness"].startswith("OFFICIAL_WEB_DISCOVERY_ONLY")
    assert "no RSS link" in circle["readiness"]
    assert "automated-access terms" in circle["readiness"]
    assert "https://www.circle.com/pressroom/circle-to-announce-q3-2026-financial-results-on-november-4-2026" in circle["documentation"]


def test_geckoterminal_candidate_preserves_access_conflicts_and_retention_limits():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    dex = next(route for route in index["task_routes"] if route["task"] == "Exact DEX pair liquidity and turnover observations")
    candidate = next(item for item in dex["source_priority"] if item["provider"] == "geckoterminal_public_api")
    assert candidate["route_contract"] is None
    assert candidate["smoke_evidence"] is None
    assert candidate["readiness"].startswith("DOCUMENTED_PUBLIC_KEYLESS_BUT_NOT_ROUTED")
    assert "10 calls/minute" in candidate["readiness"] and "30 calls/minute" in candidate["readiness"]
    assert "deletion of stored data on termination" in candidate["readiness"]
    assert "immutable research-run retention fit is unresolved" in candidate["readiness"]
    assert "CoinGecko" in candidate["upstream_lineage"]


def test_dexpaprika_historical_ohlcv_quota_denial_and_doc_conflict_are_preserved():
    contract = read_json(ROOT / ".codex/dexpaprika-routing.json")
    candidate = contract["historical_ohlcv_candidate"]
    smoke = read_json(ROOT / candidate["smoke_evidence"])
    assert candidate["runtime_readiness"] == "UNAVAILABLE_KEYLESS_QUOTA_EXHAUSTED"
    assert smoke["http_status"] == 402
    assert smoke["authorization_header_sent"] is False
    assert smoke["response_body"]["tier"] == "anonymous"
    assert "Monthly limit reached" in smoke["response_body"]["message"]
    assert smoke["attempt_policy"]["retry"] is False
    assert smoke["attempt_policy"]["credential_used"] is False
    assert smoke["retrieval_started_at_utc"] is None
    assert candidate["conflicting_official_claims"]
    index = read_json(ROOT / ".codex/data-provider-index.json")
    dex = next(route for route in index["task_routes"] if route["task"].startswith("Exact DEX"))
    paprika = next(item for item in dex["source_priority"] if item["provider"] == "dexpaprika")
    assert paprika["historical_ohlcv_readiness"].startswith("UNAVAILABLE_KEYLESS_QUOTA_EXHAUSTED")


def test_binance_vision_oi_archive_is_conditional_not_enabled_without_project_scope():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Historical derivatives funding/open interest")
    candidate = next(item for item in task["source_priority"] if item["provider"] == "binance_vision_um_metrics_archive")
    assert candidate["route_contract"] is None
    assert candidate["smoke_evidence"] is None
    assert candidate["readiness"].startswith("DOCUMENTED_FREE_BUT_CONDITIONAL_AND_UNQUALIFIED")
    assert "CC BY-NC-SA 4.0" in candidate["readiness"]
    assert "create_time convention shift" in candidate["readiness"]
    assert "exact rate/download limits" in candidate["readiness"]


def test_sourcify_is_routed_only_as_contract_verification_metadata():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Contract/token security screening")
    entry = next(item for item in task["source_priority"] if item["provider"] == "sourcify_contract_repository")
    contract = read_json(ROOT / ".codex/sourcify-routing.json")
    smoke = read_json(ROOT / contract["access"]["smoke_evidence"])
    assert entry["route_contract"] == "sourcify-routing.json"
    assert entry["purpose"].endswith("not a security verdict")
    assert entry["readiness"].startswith("RESPONDED_FOR_EXACT_BASE_USDC")
    assert smoke["provider"] == contract["provider"]
    assert smoke["identity_status"] == "VERIFIED"
    assert contract["route"]["endpoint"] == "GET /v2/contract/{chainId}/{address}"
    assert "not a security audit" in contract["route"]["interpretation"]
    assert "Do not count as independent risk corroboration" in contract["route"]["independence"]
    assert contract["request_policy"]["local_budget"] == "At most one GET per invocation; exact-contract persistent cache prevents repeat positive lookups across invocations."
    assert "No automatic security-verdict fallback" in task["fallback"]


def test_honeypot_is_rejected_without_runtime_call_under_competition_clause():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Contract/token security screening")
    entry = next(item for item in task["source_priority"] if item["provider"] == "honeypot_is_public_api")
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["request_made"] is False
    assert entry["readiness"].startswith("REJECTED_FOR_THIS_WORKFLOW_PENDING_WRITTEN_PERMISSION")
    assert entry["official_terms"] == "https://docs.honeypot.is/terms"
    assert "Honeypot.is is rejected" in task["fallback"]


def test_rugcheck_is_candidate_only_until_data_reuse_and_retention_are_resolved():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Contract/token security screening")
    entry = next(item for item in task["source_priority"] if item["provider"] == "rugcheck_solana_public_api")
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["request_made"] is False
    assert entry["endpoint_template"] == "GET https://api.rugcheck.xyz/v1/tokens/{solana_mint}/report/summary"
    assert "keyless GET" in entry["readiness"]
    assert "cached" in entry["documented_limits_and_freshness"]
    assert "one request per second" in entry["documented_limits_and_freshness"]
    assert "retention scope are established" in entry["policy"]
    assert "shared upstreams" in entry["upstream_lineage"]


def test_uniswap_blog_remains_discovery_only_without_publisher_feed_contract():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"].startswith("Catalyst"))
    entry = next(item for item in task["source_priority"] if item["provider"] == "uniswap_labs_official_blog")
    assert entry["catalog_endpoint"] == "https://blog.uniswap.org/"
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["readiness"].startswith("OFFICIAL_WEB_DISCOVERY_ONLY")
    assert "https://github.com/Uniswap/docs/issues/809" in entry["discovery_evidence"]


def test_cloudflare_ethereum_gateway_is_not_routed_without_free_entitlement():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Transfer-level stablecoin activity")
    entry = next(item for item in task["source_priority"] if item["provider"] == "cloudflare_public_ethereum_gateway")
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["request_made"] is False
    assert entry["readiness"].startswith("NOT_QUALIFIED_USAGE_BASED_GATEWAY")
    assert "paid gateway subscription" in entry["authentication"]
    assert "Do not call or integrate" in entry["policy"]


def test_live_hive_discovery_is_free_but_material_fetch_remains_uninvoked():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Transfer-level stablecoin activity")
    entry = next(item for item in task["source_priority"] if item["provider"] == "hive_tool_catalog")
    evidence = read_json(ROOT / entry["smoke_evidence"])
    assert entry["readiness"].startswith("RESPONDED_FOR_ONE_FREE_DISCOVERY_QUERY")
    assert entry["material_call_made"] is False
    assert evidence["credits_used"] == 0
    assert evidence["relevant_tool_discovery"]["credit_cost"] == 1
    assert evidence["relevant_tool_discovery"]["invoked"] is False
    assert evidence["evidence_receipt"]["receipt_id"]
    assert "no chain-wide stablecoin transfer route" in task["fallback"]


def test_hive_derivatives_archive_discovery_does_not_authorize_material_reads():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Historical derivatives funding/open interest")
    entry = next(item for item in task["source_priority"] if item["provider"] == "hive_archive_derivatives_catalog")
    evidence = read_json(ROOT / entry["smoke_evidence"])
    assert entry["route_contract"] is None
    assert entry["material_call_made"] is False
    assert evidence["credits_used"] == 0
    assert all(not tool["invoked"] and tool["credit_cost"] == 1 for tool in evidence["material_tool_discovery"])
    assert "one Hive credit" in entry["readiness"]
    assert "no material tool was invoked" in task["fallback"]


def test_kraken_futures_analytics_stays_gated_until_terms_scope_is_resolved():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Historical derivatives funding/open interest")
    entry = next(item for item in task["source_priority"] if item["provider"] == "kraken_futures_market_analytics")
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["request_made"] is False
    assert entry["endpoint_template"].startswith("GET https://futures.kraken.com/api/charts/v1/analytics/")
    assert entry["documented_intervals_seconds"] == [60, 300, 900, 1800, 3600, 14400, 43200, 86400, 604800]
    assert "no cost associated with public endpoints" in entry["request_cost"]
    assert "own benefit" in entry["policy"] and "prior permission" in entry["policy"]
    assert "workspace are not established" in entry["policy"]


def test_cryptocom_derivatives_stays_gated_until_personal_use_scope_is_resolved():
    index = read_json(ROOT / ".codex/data-provider-index.json")
    task = next(route for route in index["task_routes"] if route["task"] == "Historical derivatives funding/open interest")
    entry = next(item for item in task["source_priority"] if item["provider"] == "cryptocom_exchange_public_derivatives")
    assert entry["route_contract"] is None
    assert entry["smoke_evidence"] is None
    assert entry["request_made"] is False
    assert entry["endpoint_templates"][0].startswith("GET https://api.crypto.com/exchange/v1/public/get-valuations?")
    assert "hourly funding settlements" in entry["documented_history_and_limits"]
    assert "not historical OI" in entry["documented_history_and_limits"]
    assert "100 requests/second per method per IP" in entry["documented_history_and_limits"]
    assert "Do not query or route until Fin-Dex use scope is established" in entry["policy"]
    assert "written consent" in entry["policy"]


\n