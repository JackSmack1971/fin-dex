#!/usr/bin/env python3
"""Build an offline view of MCP tools and local REST adapter routes.

This reports configuration and retained route evidence. It never invokes a
provider and does not treat discovery, exposure, historical success, or free
documentation as proof of current connectivity or authorization.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def _defillama_invocations(task: str, root: Path) -> tuple[str, str | list[str] | None]:
    script = ".agents/skills/crypto-mcp-research/scripts/defillama_public.py"
    mapping = {
        "protocol TVL and chain breakdown":
            (f"python {script} protocol-tvl --protocol {{protocol}}; "
             f"python {script} protocol-history --protocol {{protocol}}; "
             f"python {script} chain-tvl-history --chain {{chain}}", "GET /tvl/{protocol}"),
        "fees and revenue":
            (f"python {script} fees --chain {{chain}} --data-type {{dataType}}; "
             f"python {script} protocol-fees --protocol {{protocol}} --data-type {{dataType}}",
             ["GET /overview/fees[/{chain}]", "GET /summary/fees/{protocol}"]),
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
                 "token-pairs --chain {dexScreenerChainId} --token {tokenAddress}"),
                ("pair_snapshot", "GET /latest/dex/pairs/{chainId}/{pairId}",
                 "pair --chain {dexScreenerChainId} --pair {pairAddress}"),
            ):
                exact = next((x for x in observations if x.get("endpoint") == endpoint), None)
                operation_record = local_operation(
                    provider=provider, operation_id=operation, task=route.get("task", "DEX pool observations"),
                    adapter_path=adapter_path,
                    invocation_method=f"python {adapter_rel} {command}", endpoint=endpoint,
                    runtime_evidence=runtime,
                    route_qualification=exact or {"status": "NO_EXACT_ROUTE_QUALIFICATION_RETAINED",
                                                  "provider_smoke_scope": qualification},
                    provider_qualification=provider_qualification, free_access=free_access,
                    authorization=auth)
                operation_record["chain_identifier_semantics"] = (
                    "Use the exact DEX Screener provider chainId string, not the numeric EVM chain ID; "
                    "Base is `base` (not `8453`).")
                result.append(operation_record)
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
                endpoints = endpoint if isinstance(endpoint, list) else [endpoint]
                matches = [x for x in observations if x.get("endpoint") in endpoints]
                result.append(local_operation(
                    provider=provider, operation_id=_operation_key(task), task=task,
                    adapter_path=adapter_path, invocation_method=invocation, endpoint=endpoint,
                    runtime_evidence=runtime,
                    route_qualification={"status": "HISTORICAL_QUERY_QUALIFICATION_ONLY" if matches else "NO_MATCHING_ROUTE_QUALIFICATION",
                                         "observations": matches},
                    provider_qualification=provider_qualification, free_access=free_access,
                    authorization=auth))
    return result


def _response_endpoint_matches(actual: Any, expected: Any) -> bool:
    expected_values = expected if isinstance(expected, list) else [expected]
    return actual in expected_values


def _valid_retrieval_time(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == dt.timedelta(0)


def _verify_run_response(provider: str, operation: str, expected_endpoint: Any,
                         artifact_path: str, artifact_sha256: str,
                         document: dict[str, Any]) -> dict[str, Any] | None:
    """Describe a retained adapter response without promoting static capability state."""
    endpoint = document.get("endpoint")
    retrieved = document.get("retrieved_at_utc")
    if (document.get("provider") != provider or
            not _response_endpoint_matches(endpoint, expected_endpoint) or
            not _valid_retrieval_time(retrieved) or
            not isinstance(document.get("source_url"), str) or "data" not in document):
        return None

    identity: dict[str, Any] = {"status": "NOT_RETURNED_BY_RESPONSE"}
    qualification: str | None = None
    freshness = document.get("freshness_status", "SOURCE_TIMESTAMP_NOT_ASSESSED")
    data = document["data"]
    source_url = document.get("source_url")
    if provider == "dexscreener" and operation in {"token_pair_discovery", "pair_snapshot"}:
        requested_chain = document.get("requested_chain_id")
        requested_token = document.get("requested_identifier")
        expected_count = document.get("pair_count")
        if isinstance(data, dict):
            pairs = data.get("pairs")
            if not isinstance(pairs, list) and isinstance(data.get("pair"), dict):
                pairs = [data["pair"]]
        else:
            pairs = data
        if not isinstance(pairs, list):
            return None
        parsed_url = urlparse(source_url or "")
        url_parts = parsed_url.path.split("/")
        expected_route_parts = (['', 'token-pairs', 'v1'] if operation == "token_pair_discovery"
                                else ['', 'latest', 'dex', 'pairs'])
        expected_part_count = len(expected_route_parts) + 2
        url_identity_matches = (
            parsed_url.scheme == "https" and parsed_url.netloc == "api.dexscreener.com" and
            url_parts[:len(expected_route_parts)] == expected_route_parts and
            len(url_parts) == expected_part_count and not parsed_url.query and not parsed_url.fragment and
            url_parts[-2] == requested_chain and
            url_parts[-1].lower() == str(requested_token).lower()
        )
        def matches_requested(pair: Any) -> bool:
            if not isinstance(pair, dict) or pair.get("chainId") != requested_chain:
                return False
            if operation == "pair_snapshot":
                returned = pair.get("pairAddress")
                return isinstance(returned, str) and returned.lower() == str(requested_token).lower()
            return any(
                isinstance(pair.get(side), dict) and
                str(pair[side].get("address", "")).lower() == str(requested_token).lower()
                for side in ("baseToken", "quoteToken"))
        identity_matches = (
            isinstance(requested_chain, str) and isinstance(requested_token, str) and
            isinstance(expected_count, int) and not isinstance(expected_count, bool) and
            expected_count == len(pairs) and any(matches_requested(pair) for pair in pairs) and
            all(matches_requested(pair) for pair in pairs) and url_identity_matches
        )
        if not identity_matches:
            return None
        identity = {"status": "RESPONSE_IDENTITY_MATCHED", "chain_id": requested_chain,
                    ("pair_address" if operation == "pair_snapshot" else "token_address"): requested_token}
        qualification = "RESPONDED_ROUTE_IDENTITY_MATCHED"
    elif provider == "defillama":
        parsed = urlparse(source_url or "")
        route_slug = parsed.path.rstrip("/").split("/")[-1] if parsed.path else ""
        if endpoint == "GET /tvl/{protocol}":
            if not isinstance(data, (int, float)) or isinstance(data, bool) or not math.isfinite(data):
                return None
            if (parsed.scheme != "https" or parsed.netloc != "api.llama.fi" or
                    parsed.path != f"/tvl/{route_slug}" or not route_slug):
                return None
            identity = {"status": "QUERY_SLUG_ONLY", "protocol_slug": route_slug,
                        "response_does_not_return_protocol_identity": True}
            qualification = "RESPONDED_ROUTE_SHAPE_MATCHED_IDENTITY_LIMITED"
        elif endpoint == "GET /summary/fees/{protocol}":
            query = parse_qs(parsed.query)
            if (not isinstance(data, dict) or not isinstance(data.get("name"), str) or
                    not isinstance(data.get("address"), str) or
                    query.get("dataType") != ["dailyRevenue"] or
                    parsed.scheme != "https" or parsed.netloc != "api.llama.fi" or
                    parsed.path != f"/summary/fees/{route_slug}" or not route_slug or
                    re.sub(r"[^a-z0-9]", "", data["name"].lower()) !=
                    re.sub(r"[^a-z0-9]", "", route_slug.lower())):
                return None
            identity = {"status": "RESPONSE_IDENTITY_RETURNED", "protocol_slug": route_slug,
                        "name": data["name"], "symbol": data.get("symbol"),
                        "address": data["address"], "chains": data.get("chains")}
            qualification = "RESPONDED_ROUTE_QUERY_MATCHED_IDENTITY_RETURNED"
        else:
            return None
    else:
        return None

    if qualification is None:
        return None

    return {
        "status": qualification,
        "transport": "LOCAL_REST_ADAPTER",
        "mcp_registered": False,
        "endpoint": endpoint,
        "source_url": source_url,
        "retrieved_at_utc": retrieved,
        "identity": identity,
        "freshness": freshness,
        "artifact": artifact_path,
        "artifact_sha256": artifact_sha256,
        "interpretation": "Retained response evidence for this exact query; does not establish current connectivity or provider-wide qualification.",
    }


def _attach_run_responses(operations: list[dict[str, Any]], run_dir: Path | None) -> None:
    if run_dir is None:
        return
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")
    for operation in operations:
        operation["run_response_evidence"] = []
        operation["run_evidence_scope"] = "RETAINED_RUN_ARTIFACTS_ONLY; NOT_A_CURRENT_SESSION_PROBE"
    for path in sorted(run_dir.glob("*.json")):
        try:
            resolved_path = path.resolve()
            if not resolved_path.is_relative_to(run_dir):
                continue
            if resolved_path.stat().st_size > 5_500_000:
                continue
            raw = resolved_path.read_bytes()
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict):
                continue
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        for operation in operations:
            if operation["transport"] != "LOCAL_REST_ADAPTER":
                continue
            evidence = _verify_run_response(operation["provider"], operation["operation"],
                                            operation.get("endpoint"), path.name,
                                            hashlib.sha256(raw).hexdigest(), document)
            if evidence is not None:
                operation["run_response_evidence"].append(evidence)


def build_effective_view(root: Path = REPO_ROOT, run_dir: Path | None = None) -> dict[str, Any]:
    """Build normalized local capability metadata without making network calls."""
    root = Path(root).resolve()
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required")
    config = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))
    inventory = _read_json(root / ".codex/provider-capabilities.json")
    operations = _mcp_operations(config, inventory) + _local_operations(root)
    _attach_run_responses(operations, run_dir)
    return {
        "schema_version": 1,
        "generated_by": "scripts/effective_capabilities.py",
        "network_calls_made": False,
        "interpretation": [
            "MCP inventory records native MCP tools only; local adapters are separate operations.",
            "Exposure is not provider authorization. Historical response evidence is not current connectivity.",
            "Route qualification never promotes every route or the provider to qualified status.",
        ],
        "operations": operations,
    }


DIMENSIONS = ("configured", "registered", "runtime_discovered", "model_exposed",
              "invocation_authorized", "authentication_established", "provider_responded",
              "query_qualified")


def build_diagnostic_view(root: Path = REPO_ROOT, run_dir: Path | None = None,
                          generated_at: str | None = None) -> dict[str, Any]:
    """Build opt-in v2 diagnostics. Static files never prove runtime properties."""
    base = build_effective_view(root, run_dir)
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not _valid_retrieval_time(generated_at):
        raise ValueError("generated_at must be a UTC timestamp ending in Z")
    operations = []
    for old in base["operations"]:
        is_mcp = old["transport"] == "MCP"
        configured = old.get("mcp_registered") is True if is_mcp else old.get("adapter_discovered") is True
        exposed = old.get("authorization", "").startswith("EXPOSED_BY_PROJECT_ALLOWLIST") if is_mcp else False
        evidence = [{"id": "configuration", "source": "ACTIVE_PROJECT_CONFIGURATION",
                     "scope": ("exact provider/operation configured" if configured else
                               "checked active configuration; exact provider/operation absent or disabled"),
                     "observed_at": None, "retrieved_at": None}]
        states = {key: {"state": "UNKNOWN", "evidence_refs": []} for key in DIMENSIONS}
        states["configured"] = {"state": "YES" if configured else "NO",
                                 "evidence_refs": ["configuration"]}
        states["registered"] = {"state": "UNKNOWN", "evidence_refs": []}
        states["model_exposed"] = {"state": "UNKNOWN", "evidence_refs": []}
        states["invocation_authorized"] = {"state": "UNKNOWN", "evidence_refs": []}
        states["authentication_established"] = {"state": "UNKNOWN", "evidence_refs": []}
        states["provider_responded"] = {"state": "UNKNOWN", "evidence_refs": []}
        states["query_qualified"] = {"state": "UNKNOWN", "evidence_refs": []}
        if is_mcp and exposed:
            evidence.append({"id": "allowlist", "source": "ACTIVE_PROJECT_CONFIGURATION",
                             "scope": "exact advertised tool name only", "observed_at": None,
                             "retrieved_at": None})
        evidence.append({"id": "generation", "source": "DIAGNOSTIC_GENERATION",
                         "scope": "report only; does not refresh evidence", "observed_at": None,
                         "retrieved_at": generated_at})
        operations.append({
            "canonical_identity": old["canonical_operation_id"],
            "provider": old["provider"], "operation": old["operation"], "transport": old["transport"],
            "configuration_origin": (".codex/config.toml and .codex/provider-capabilities.json" if is_mcp
                                     else ".codex provider routing contract"),
            "states": states, "evidence": evidence,
            "generated_at": generated_at, "age_seconds": None,
            "freshness": "UNKNOWN", "freshness_scope": "NO_APPLICABLE_SOURCE_TTL",
            "source_ttl_seconds": None,
            "side_effect_uncertainty": "UNKNOWN", "read_only_eligible": "UNKNOWN",
            "invocation_arguments_sha256": None,
        })
    return {"schema_version": 2, "format": "capability-diagnostic-v2",
            "generated_at": generated_at, "network_calls_made": False,
            "historical_inventory_count": 85,
            "historical_inventory_note": "85-operation inventory is a historical snapshot, not proof of current availability.",
            "operations": operations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--provider", help="filter to one canonical provider id")
    parser.add_argument("--run-dir", type=Path,
                        help="attach validated retained adapter responses from this run (no network calls)")
    parser.add_argument("--format", choices=("v1", "v2"), default="v1",
                        help="output format (default v1; v2 is opt-in diagnostics)")
    parser.add_argument("--generated-at", help="fixed UTC generation time for deterministic v2 output")
    args = parser.parse_args()
    try:
        result = (build_diagnostic_view(args.root, args.run_dir, args.generated_at)
                  if args.format == "v2" else build_effective_view(args.root, args.run_dir))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 2
    if args.provider:
        result["operations"] = [x for x in result["operations"] if x.get("provider") == args.provider]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
