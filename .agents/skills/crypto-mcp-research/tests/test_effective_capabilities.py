import importlib.util
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "effective_capabilities", ROOT / "scripts" / "effective_capabilities.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


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
    assert "dexscreener_public.py pair" in dex["invocation_method"]
    assert llama["adapter_discovered"] is True
    assert llama["transport"] == "LOCAL_REST_ADAPTER"
    assert llama["mcp_registered"] is False
    assert llama["historical_git_presence"] == "NOT_INSPECTED"
    assert llama["route_qualification"]["status"] == "HISTORICAL_QUERY_QUALIFICATION_ONLY"
    assert mcp_llama == []
    assert view["network_calls_made"] is False
