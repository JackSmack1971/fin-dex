import importlib.util
import json
from pathlib import Path
import tempfile
import hashlib
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "effective_capabilities", ROOT / "scripts" / "effective_capabilities.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_capabilities", ROOT / "scripts" / "validate_capabilities.py")
validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(validator)


def operation(tmp_path, *, credentials_required=False, credentials_available=False):
    adapter = tmp_path / "adapter.py"
    adapter.write_text("# offline test adapter\n", encoding="utf-8")
    return module.local_operation(
        provider="fixture_provider", operation_id="route-a", task="fixture task",
        adapter_path=adapter, invocation_method="python adapter.py read",
        endpoint="GET /fixture", runtime_evidence={"status": "NOT_PROBED"},
        route_qualification={"status": "QUALIFIED_FOR_EXACT_FIXTURE_ROUTE"},
        provider_qualification="PARTIALLY_QUALIFIED",
        free_access={"evidence": "fixture documentation", "limits": "one read"},
        authorization={"credentials_required": credentials_required,
                       "credentials_available": credentials_available})


def test_local_adapter_missing_is_not_reported_as_mcp_or_provider_unavailable(tmp_path):
    result = module.local_operation(
        provider="fixture_provider", operation_id="route-a", task="fixture task",
        adapter_path=tmp_path / "missing.py", invocation_method="python missing.py",
        endpoint=None, runtime_evidence={"status": "NOT_PROBED"},
        route_qualification={"status": "NOT_ESTABLISHED"},
        provider_qualification="NOT_ESTABLISHED", free_access={}, authorization={})
    assert result["adapter_discovered"] is False
    assert result["runtime_availability"] == "MISSING_LOCAL_ADAPTER"
    assert result["mcp_registered"] is False


def test_discovered_adapter_is_blocked_when_required_credentials_are_unavailable(tmp_path):
    result = operation(tmp_path, credentials_required=True, credentials_available=False)
    assert result["adapter_discovered"] is True
    assert result["runtime_availability"] == "BLOCKED_AUTHORIZATION"
    assert result["authorization"] == "NOT_AUTHORIZED_CREDENTIALS_UNAVAILABLE"


def test_route_qualification_does_not_promote_provider_qualification(tmp_path):
    result = operation(tmp_path)
    assert result["route_qualification"]["status"] == "QUALIFIED_FOR_EXACT_FIXTURE_ROUTE"
    assert result["provider_qualification"] == "PARTIALLY_QUALIFIED"


def test_unknown_authorization_stays_unverified(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text("# adapter present\n", encoding="utf-8")
    result = module.local_operation(
        provider="fixture_provider", operation_id="route-a", task="fixture task",
        adapter_path=adapter, invocation_method="python adapter.py read",
        endpoint="GET /fixture", runtime_evidence={"status": "NOT_PROBED"},
        route_qualification={}, provider_qualification="NOT_ESTABLISHED",
        free_access={}, authorization={"credentials_required": "UNVERIFIED",
                                       "credentials_available": "UNVERIFIED"})
    assert result["authorization"] == "UNVERIFIED"
    assert result["runtime_availability"] == "NOT_PROBED"


def test_mcp_inventory_does_not_claim_registration_when_server_disabled():
    inventory = {"providers": [{"id": "fixture", "tools": [{"name": "read"}]}]}
    operations = module._mcp_operations(
        {"mcp_servers": {"fixture": {"enabled": False, "enabled_tools": ["read"]}}}, inventory)
    assert operations[0]["inventory_listed"] is True
    assert operations[0]["mcp_registered"] is False
    assert operations[0]["registration_evidence"] == "ABSENT_OR_DISABLED_IN_ACTIVE_PROJECT_CONFIG"


def test_real_effective_view_separates_local_rest_adapters_from_mcp_tools():
    view = module.build_effective_view()
    operations = view["operations"]
    dex = next(x for x in operations if x["canonical_operation_id"] == "dexscreener:pair_snapshot")
    llama = next(x for x in operations if x["canonical_operation_id"] ==
                 "defillama:protocol_tvl_and_chain_breakdown")
    mcp_llama = [x for x in operations if x["provider"] == "defillama" and x["transport"] == "MCP"]
    assert dex["adapter_discovered"] is True
    assert dex["adapter_code_state"] == "PRESENT_IN_CHECKED_OUT_TREE"
    assert dex["mcp_registered"] is False
    assert "dexscreener_public.py pair --chain {dexScreenerChainId}" in dex["invocation_method"]
    assert "Base is `base` (not `8453`)" in dex["chain_identifier_semantics"]
    assert llama["adapter_discovered"] is True
    assert llama["transport"] == "LOCAL_REST_ADAPTER"
    assert llama["mcp_registered"] is False
    assert llama["historical_git_presence"] == "NOT_INSPECTED"
    assert llama["route_qualification"]["status"] == "HISTORICAL_QUERY_QUALIFICATION_ONLY"
    fees = next(x for x in operations if x["canonical_operation_id"] == "defillama:fees_and_revenue")
    assert "GET /summary/fees/{protocol}" in fees["endpoint"]
    assert "protocol-fees --protocol {protocol}" in fees["invocation_method"]
    assert mcp_llama == []
    assert view["network_calls_made"] is False


def test_run_responses_attach_exact_local_route_evidence_without_promoting_capability(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    token = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
    dex_doc = {
        "provider": "dexscreener",
        "endpoint": "GET /token-pairs/v1/{chainId}/{tokenAddress}",
        "source_url": f"https://api.dexscreener.com/token-pairs/v1/base/{token}",
        "requested_chain_id": "base", "requested_identifier": token,
        "retrieved_at_utc": "2026-10-18T05:42:45Z", "pair_count": 1,
        "data": [{"chainId": "base", "baseToken": {"address": token},
                  "quoteToken": {"address": "0x111"}}],
    }
    dex_path = run_dir / "dex.json"
    dex_path.write_text(json.dumps(dex_doc), encoding="utf-8")
    operations = [{
        "provider": "dexscreener", "operation": "token_pair_discovery",
        "transport": "LOCAL_REST_ADAPTER", "endpoint": dex_doc["endpoint"],
        "provider_qualification": "PARTIALLY_QUALIFIED",
        "runtime_availability": "HISTORICAL_RESPONSE_ONLY", "mcp_registered": False,
    }]

    module._attach_run_responses(operations, run_dir)

    evidence = operations[0]["run_response_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["status"] == "RESPONDED_ROUTE_IDENTITY_MATCHED"
    assert evidence[0]["identity"] == {
        "status": "RESPONSE_IDENTITY_MATCHED", "chain_id": "base", "token_address": token}
    assert evidence[0]["artifact_sha256"] == hashlib.sha256(dex_path.read_bytes()).hexdigest()
    assert operations[0]["provider_qualification"] == "PARTIALLY_QUALIFIED"
    assert operations[0]["runtime_availability"] == "HISTORICAL_RESPONSE_ONLY"
    assert operations[0]["mcp_registered"] is False
    assert operations[0]["run_evidence_scope"] == "RETAINED_RUN_ARTIFACTS_ONLY; NOT_A_CURRENT_SESSION_PROBE"

    dex_doc["source_url"] = (
        f"https://api.dexscreener.com/token-pairs/v1/extra/base/{token}")
    dex_path.write_text(json.dumps(dex_doc), encoding="utf-8")
    module._attach_run_responses(operations, run_dir)
    assert operations[0]["run_response_evidence"] == []


def test_defillama_protocol_summary_retains_exact_route_and_response_identity(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    document = {
        "provider": "defillama", "endpoint": "GET /summary/fees/{protocol}",
        "source_url": "https://api.llama.fi/summary/fees/aerodrome?dataType=dailyRevenue",
        "retrieved_at_utc": "2026-10-18T05:42:56Z",
        "data": {"name": "Aerodrome", "symbol": "AERO",
                 "address": "base:0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                 "chains": ["Base", "Arc"]},
    }
    (run_dir / "fees.json").write_text(json.dumps(document), encoding="utf-8")
    operation = [{"provider": "defillama", "operation": "fees_and_revenue",
                  "transport": "LOCAL_REST_ADAPTER",
                  "endpoint": ["GET /overview/fees[/{chain}]", "GET /summary/fees/{protocol}"],
                  "provider_qualification": "PARTIALLY_QUALIFIED",
                  "runtime_availability": "HISTORICAL_RESPONSE_ONLY", "mcp_registered": False}]

    module._attach_run_responses(operation, run_dir)

    evidence = operation[0]["run_response_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["status"] == "RESPONDED_ROUTE_QUERY_MATCHED_IDENTITY_RETURNED"
    assert evidence[0]["identity"]["address"].lower().endswith("0x940181a94a35a4569e4529a3cdfb74e38fd98631")
    assert operation[0]["provider_qualification"] == "PARTIALLY_QUALIFIED"


def test_dex_pair_snapshot_requires_requested_pair_identity(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    pair_address = "0xabc123"
    doc = {"provider": "dexscreener", "endpoint": "GET /latest/dex/pairs/{chainId}/{pairId}",
           "source_url": "https://api.dexscreener.com/latest/dex/pairs/base/0xabc123",
           "requested_chain_id": "base", "requested_identifier": pair_address,
           "retrieved_at_utc": "2026-10-18T05:42:45Z", "pair_count": 1,
           "data": [{"chainId": "base", "pairAddress": pair_address}]}
    (run_dir / "pair.json").write_text(json.dumps(doc), encoding="utf-8")
    operation = [{"provider": "dexscreener", "operation": "pair_snapshot",
                  "transport": "LOCAL_REST_ADAPTER", "endpoint": doc["endpoint"]}]

    module._attach_run_responses(operation, run_dir)

    assert operation[0]["run_response_evidence"][0]["identity"] == {
        "status": "RESPONSE_IDENTITY_MATCHED", "chain_id": "base", "pair_address": pair_address}

    doc["data"][0]["pairAddress"] = "0xdifferent"
    (run_dir / "pair.json").write_text(json.dumps(doc), encoding="utf-8")
    module._attach_run_responses(operation, run_dir)
    assert operation[0]["run_response_evidence"] == []

    doc["data"][0]["pairAddress"] = pair_address
    doc["source_url"] = "https://api.dexscreener.com/latest/dex/pairs/extra/base/0xabc123"
    (run_dir / "pair.json").write_text(json.dumps(doc), encoding="utf-8")
    module._attach_run_responses(operation, run_dir)
    assert operation[0]["run_response_evidence"] == []


def test_v1_remains_default_and_v2_is_fixed_time_offline():
    fixture = json.loads((ROOT / "tests" / "fixtures" /
                          "capability-diagnostic-v2-input.json").read_text(encoding="utf-8"))
    fixed = fixture["generated_at"]
    first = module.build_diagnostic_view(generated_at=fixed)
    second = module.build_diagnostic_view(generated_at=fixed)
    assert first == second
    assert first["schema_version"] == 2
    assert first["historical_inventory_count"] == 85
    assert first["network_calls_made"] is False
    assert module.build_effective_view()["schema_version"] == 1
    assert all(x["states"]["registered"]["state"] == "UNKNOWN" for x in first["operations"])
    assert all(x["states"]["invocation_authorized"]["state"] == "UNKNOWN" for x in first["operations"])
    assert all(x["freshness"] == "UNKNOWN" for x in first["operations"])
    assert validator.validate_diagnostic(first) == []


def test_v2_rejects_static_capability_promotion_and_bad_freshness():
    report = module.build_diagnostic_view(generated_at="2026-10-18T12:00:00Z")
    op = report["operations"][0]
    op["states"]["provider_responded"] = {"state": "YES", "evidence_refs": ["configuration"]}
    op["freshness"] = "FRESH"
    errors = validator.validate_diagnostic(report)
    assert any("static evidence cannot establish YES" in e for e in errors)
    assert any("freshness must be UNKNOWN" in e for e in errors)


def test_v2_authorization_is_bound_to_exact_invocation_arguments():
    report = module.build_diagnostic_view(generated_at="2026-10-18T12:00:00Z")
    op = report["operations"][0]
    op["states"]["invocation_authorized"] = {"state": "YES", "evidence_refs": ["configuration"]}
    errors = validator.validate_diagnostic(report)
    assert any("exact invocation argument digest" in e for e in errors)
    assert any("exact-argument evidence scope" in e for e in errors)
