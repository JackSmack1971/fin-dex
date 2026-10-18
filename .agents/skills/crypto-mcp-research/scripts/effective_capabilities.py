#!/usr/bin/env python3
"""Build an offline view of MCP tools and local REST adapter routes.

This reports configuration and retained route evidence. It never invokes a
provider and does not treat discovery, exposure, historical success, or free
documentation as proof of current connectivity or authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - supported Python includes tomllib
    tomllib = None


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
LOCAL_ADAPTERS = (
    ("dexscreener", ".codex/dexscreener-routing.json",
     ".agents/skills/crypto-mcp-research/scripts/dexscreener_public.py"),
    ("dexpaprika", ".codex/dexpaprika-routing.json",
     ".agents/skills/crypto-mcp-research/scripts/dexpaprika_public.py"),
    ("defillama", ".codex/data-provider-routing.json",
     ".agents/skills/crypto-mcp-research/scripts/defillama_public.py"),
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def local_operation(*, provider: str, operation_id: str, task: str,
                    adapter_path: Path, invocation_method: str,
                    endpoint: str | None, runtime_evidence: dict[str, Any],
                    route_qualification: dict[str, Any],
                    provider_qualification: str,
                    free_access: dict[str, Any],
                    authorization: dict[str, Any]) -> dict[str, Any]:
    """Normalize one local route while keeping discovery, access, and proof apart."""
    discovered = adapter_path.is_file()
    credentials_required = authorization.get("credentials_required")
    credentials_available = authorization.get("credentials_available")
    if not discovered:
        authorization_status = "NOT_EVALUATED_ADAPTER_MISSING"
        availability = "MISSING_LOCAL_ADAPTER"
    elif credentials_required is True and credentials_available is not True:
        authorization_status = "NOT_AUTHORIZED_CREDENTIALS_UNAVAILABLE"
        availability = "BLOCKED_AUTHORIZATION"
    elif credentials_required is False:
        authorization_status = "NO_CREDENTIAL_REQUIRED; session/network permission not established"
        availability = runtime_evidence.get("status", "NOT_PROBED")
    else:
        authorization_status = "UNVERIFIED"
        availability = runtime_evidence.get("status", "NOT_PROBED")
    return {
        "canonical_operation_id": f"{provider}:{operation_id}",
        "provider": provider,
        "operation": operation_id,
        "transport": "LOCAL_REST_ADAPTER",
        "mcp_registered": False,
        "adapter_path": str(adapter_path),
        "adapter_discovered": discovered,
        "adapter_code_state": ("PRESENT_IN_CHECKED_OUT_TREE" if discovered
                               else "NOT_PRESENT_IN_CHECKED_OUT_TREE"),
        "historical_git_presence": "NOT_INSPECTED",
        "invocation_method": invocation_method,
        "endpoint": endpoint,
        "runtime_availability": availability,
        "runtime_evidence": runtime_evidence,
        "authorization": authorization_status,
        "authorization_evidence": authorization,
        "capability_evidence_scope": "OFFLINE_LOCAL_CONFIGURATION_AND_RETAINED_ARTIFACTS; NO_LIVE_PROBE",
        "provider_qualification": provider_qualification,
        "route_qualification": route_qualification,
        "free_access_and_limits": free_access,
        "supported_research_tasks": [task],
    }


def _mcp_operations(config: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config.get("mcp_servers", {})
    indexed = {p.get("id"): p for p in inventory.get("providers", [])
               if isinstance(p, dict) and isinstance(p.get("id"), str)}
    output = []
    for provider, capability in indexed.items():
        server = servers.get(provider)
        registered = isinstance(server, dict) and server.get("enabled") is True
        server = server if isinstance(server, dict) else {}
        exposed = set(server.get("enabled_tools", []))
        for tool in capability.get("tools", []):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            name = tool["name"]
            identity = tool.get("identity", f"mcp__{provider}__{name}")
            responded = capability.get("responded_evidence") or {}
            exact_response = responded.get("tool_identity") == identity and responded.get("outcome") == "success"
            output.append({
                "canonical_operation_id": identity,
                "provider": provider,
                "operation": name,
                "transport": "MCP",
                "inventory_listed": True,
                "mcp_registered": registered,
                "registration_evidence": "ACTIVE_PROJECT_CONFIG" if registered else
                                         "ABSENT_OR_DISABLED_IN_ACTIVE_PROJECT_CONFIG",
                "invocation_method": f"{identity}(...) using the active tool schema",
                "runtime_availability": "RESPONDED_FOR_THIS_TOOL" if exact_response else "DISCOVERED_METADATA_ONLY",
                "provider_status": capability.get("status", "UNKNOWN"),
                "provider_qualification": capability.get("qualification_evidence") or "NOT_ESTABLISHED",
                "authorization": ("EXPOSED_BY_PROJECT_ALLOWLIST; connection grant/entitlement unverified"
                                  if name in exposed else "NOT_EXPOSED_BY_PROJECT_ALLOWLIST"),
                "route_qualification": "NOT_ESTABLISHED_FOR_THIS_OPERATION",
                "free_access_and_limits": "NOT_ESTABLISHED_BY_LOCAL_MCP_INVENTORY",
                "supported_research_tasks": [tool.get("category", "research data")],
            })
    return output


def _route_observations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    access = contract.get("access", {})
    observations = access.get("endpoint_observations", [])
    if not isinstance(observations, list):
        observations = []
    smoke = access.get("smoke_evidence")
    if isinstance(smoke, dict):
        observations = [smoke, *observations]
    return [x for x in observations if isinstance(x, dict)]


def _operation_key(task: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", task.lower()).strip("_") or "documented_route"


def _defillama_invocations(task: str, root: Path) -> tuple[str, str | None]:
    script = ".agents/skills/crypto-mcp-research/scripts/defillama_public.py"
    mapping = {
        "protocol TVL and chain breakdown":
            (f"python {script} protocol-tvl --protocol {{protocol}}; "
             f"python {script} protocol-history --protocol {{protocol}}; "
             f"python {script} chain-tvl-history --chain {{chain}}", "GET /tvl/{protocol}"),
        "fees and revenue":
            (f"python {script} fees --chain {{chain}} --data-type {{dataType}}; "
             f"python {script} protocol-fees --protocol {{protocol}} --data-type {{dataType}}",
             "GET /overview/fees[/{chain}]"),
        "stablecoin supply and flows":
            (f"python {script} stablecoins; python {script} stablecoin-chains; "
             f"python {script} stablecoin-history --chain {{chain}}", "GET /stablecoincharts/{chain}"),
        "historical open interest context":
            (f"python {script} open-interest", "GET /overview/open-interest"),
    }
    return mapping.get(task, (f"python {script} <documented-command>", None))


def _local_operations(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for provider, route_rel, adapter_rel in LOCAL_ADAPTERS:
        route_path = root / route_rel
        if not route_path.is_file():
            continue
        contract = _read_json(route_path)
        adapter_path = root / adapter_rel
        access = contract.get("access", {})
        provider_qualification = access.get("runtime_readiness", "NOT_ESTABLISHED")
        auth_text = str(access.get("auth", access.get("authorization", "unknown"))).lower()
        if any(x in auth_text for x in ("none", "anonymous", "keyless")):
            auth = {"status": "NO_CREDENTIAL_REQUIRED", "credentials_required": False,
                    "credentials_available": "NOT_APPLICABLE"}
        elif any(x in auth_text for x in ("required", "api key", "apikey", "credential")):
            auth = {"status": "CREDENTIALS_REQUIRED_AVAILABILITY_UNVERIFIED",
                    "credentials_required": True, "credentials_available": "UNVERIFIED"}
        else:
            auth = {"status": "AUTHORIZATION_UNVERIFIED", "credentials_required": "UNVERIFIED",
                    "credentials_available": "UNVERIFIED"}
        status = access.get("keyless_runtime_status")
        if not status:
            status = "HISTORICAL_RESPONSE_ONLY" if _route_observations(contract) else "NOT_PROBED"
        runtime = {"status": status, "current_session_probe": False}
        free_access = {
            "evidence": contract.get("free_access_evidence", contract.get("documentation")),
            "request_policy": contract.get("request_policy", {}),
            "contract_path": route_rel,
        }
        observations = _route_observations(contract)

        if provider == "dexscreener":
            route = (contract.get("routing") or [{}])[0]
            qualification = access.get("smoke_evidence", {})
            for operation, endpoint, command in (
                ("token_pair_discovery", "GET /token-pairs/v1/{chainId}/{tokenAddress}",
                 "token-pairs --chain {chainId} --token {tokenAddress}"),
                ("pair_snapshot", "GET /latest/dex/pairs/{chainId}/{pairId}",
                 "pair --chain {chainId} --pair {pairAddress}"),
            ):
                exact = next((x for x in observations if x.get("endpoint") == endpoint), None)
                result.append(local_operation(
                    provider=provider, operation_id=operation, task=route.get("task", "DEX pool observations"),
                    adapter_path=adapter_path,
                    invocation_method=f"python {adapter_rel} {command}", endpoint=endpoint,
                    runtime_evidence=runtime,
                    route_qualification=exact or {"status": "NO_EXACT_ROUTE_QUALIFICATION_RETAINED",
                                                  "provider_smoke_scope": qualification},
                    provider_qualification=provider_qualification, free_access=free_access,
                    authorization=auth))
        elif provider == "dexpaprika":
            for index, route in enumerate(contract.get("supported_routes", [])):
                command = route.get("tool")
                endpoint = route.get("rest_endpoint")
                evidence = route.get("readiness", route.get("qualification"))
                smoke = contract.get("smoke_evidence", {})
                qualification = {"status": evidence or "NOT_ESTABLISHED",
                                 "smoke_evidence": smoke if index == 0 else None}
                result.append(local_operation(
                    provider=provider, operation_id=_operation_key(route.get("task", f"route {index + 1}")),
                    task=route.get("task", "DEX pool data"), adapter_path=adapter_path,
                    invocation_method=command or f"python {adapter_rel} <documented-command>", endpoint=endpoint,
                    runtime_evidence=runtime, route_qualification=qualification,
                    provider_qualification=provider_qualification, free_access=free_access,
                    authorization=auth))
        else:
            for index, route in enumerate(contract.get("routing", [])):
                task = route.get("task", "DeFi protocol context")
                invocation, endpoint = _defillama_invocations(task, root)
                matches = [x for x in observations if endpoint and endpoint in str(x.get("endpoint", ""))]
                result.append(local_operation(
                    provider=provider, operation_id=_operation_key(task), task=task,
                    adapter_path=adapter_path, invocation_method=invocation, endpoint=endpoint,
                    runtime_evidence=runtime,
                    route_qualification={"status": "HISTORICAL_QUERY_QUALIFICATION_ONLY" if matches else "NO_MATCHING_ROUTE_QUALIFICATION",
                                         "observations": matches},
                    provider_qualification=provider_qualification, free_access=free_access,
                    authorization=auth))
    return result


def build_effective_view(root: Path = REPO_ROOT) -> dict[str, Any]:
    """Build normalized local capability metadata without making network calls."""
    root = Path(root).resolve()
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required")
    config = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))
    inventory = _read_json(root / ".codex/provider-capabilities.json")
    return {
        "schema_version": 1,
        "generated_by": "scripts/effective_capabilities.py",
        "network_calls_made": False,
        "interpretation": [
            "MCP inventory records native MCP tools only; local adapters are separate operations.",
            "Exposure is not provider authorization. Historical response evidence is not current connectivity.",
            "Route qualification never promotes every route or the provider to qualified status.",
        ],
        "operations": _mcp_operations(config, inventory) + _local_operations(root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--provider", help="filter to one canonical provider id")
    args = parser.parse_args()
    try:
        result = build_effective_view(args.root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 2
    if args.provider:
        result["operations"] = [x for x in result["operations"] if x["provider"] == args.provider]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
