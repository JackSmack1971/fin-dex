#!/usr/bin/env python3
"""Offline MCP capability-policy validator. Never contacts configured providers."""
import argparse
import datetime as dt
import json
import pathlib
import sys

try:
    import tomllib
except ImportError:
    tomllib = None

EXPECTED_ENDPOINTS = {
    "coingecko": "https://mcp.api.coingecko.com/mcp",
    "coinpaprika": "https://mcp.coinpaprika.com/streamable-http",
    "dexpaprika": "https://mcp.dexpaprika.com/streamable-http",
    "cryptocom_market": "https://mcp.crypto.com/market-data/mcp",
    "hive": "https://mcp.hiveintelligence.xyz/mcp",
    "gdelt": "https://gdelt.caseyjhand.com/mcp",
}
STATUSES = {"CONFIGURED", "DISCOVERED", "RESPONDED", "QUALIFIED", "UNAVAILABLE"}
EFFECTS = {"READ_ONLY", "STATEFUL", "UNKNOWN"}
PROHIBITED_CATEGORIES = {"feedback", "wallet", "trading", "credential_change", "stateful", "unknown_effect"}
DIAGNOSTIC_STATES = {"YES", "NO", "UNKNOWN", "UNSUPPORTED"}
DIAGNOSTIC_DIMENSIONS = {"configured", "registered", "runtime_discovered", "model_exposed",
                         "invocation_authorized", "authentication_established",
                         "provider_responded", "query_qualified"}


def validate_diagnostic(report):
    """Validate the v2 diagnostic contract without promoting offline evidence."""
    errors = []
    if not isinstance(report, dict) or report.get("schema_version") != 2 or report.get("format") != "capability-diagnostic-v2":
        return ["diagnostic: schema_version 2 capability-diagnostic-v2 object required"]
    if not _timestamp(report.get("generated_at")):
        errors.append("diagnostic: generated_at must be a UTC timestamp")
    if report.get("network_calls_made") is not False:
        errors.append("diagnostic: offline generation must declare network_calls_made false")
    operations = report.get("operations")
    if not isinstance(operations, list):
        return errors + ["diagnostic: operations must be an array"]
    identities = set()
    for i, op in enumerate(operations):
        prefix = f"diagnostic.operations[{i}]"
        if not isinstance(op, dict) or not isinstance(op.get("canonical_identity"), str) or not op["canonical_identity"]:
            errors.append(f"{prefix}: canonical_identity required")
            continue
        provider, operation, transport = op.get("provider"), op.get("operation"), op.get("transport")
        expected_identity = (f"mcp__{provider}__{operation}" if transport == "MCP"
                             else f"{provider}:{operation}" if transport == "LOCAL_REST_ADAPTER"
                             else None)
        if not isinstance(provider, str) or not isinstance(operation, str) or op["canonical_identity"] != expected_identity:
            errors.append(f"{prefix}: canonical identity must match exact provider, operation, and transport")
        if op["canonical_identity"] in identities:
            errors.append(f"{prefix}: duplicate canonical identity")
        identities.add(op["canonical_identity"])
        evidence = op.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"{prefix}: evidence must be an array")
            evidence = []
        indexed = {e.get("id"): e for e in evidence if isinstance(e, dict) and isinstance(e.get("id"), str)}
        if len(indexed) != len(evidence):
            errors.append(f"{prefix}: evidence ids must be present and unique")
        for evidence_id, item in indexed.items():
            for time_key in ("observed_at", "retrieved_at"):
                if item.get(time_key) is not None and not _timestamp(item.get(time_key)):
                    errors.append(f"{prefix}.evidence[{evidence_id}]: {time_key} must be UTC or null")
        states = op.get("states")
        if not isinstance(states, dict) or set(states) != DIAGNOSTIC_DIMENSIONS:
            errors.append(f"{prefix}: states must contain each defined dimension exactly once")
            continue
        for dim, record in states.items():
            if not isinstance(record, dict) or record.get("state") not in DIAGNOSTIC_STATES:
                errors.append(f"{prefix}.{dim}: invalid state")
                continue
            if record["state"] == "UNSUPPORTED" and dim != "authentication_established":
                errors.append(f"{prefix}.{dim}: UNSUPPORTED is only valid for authentication")
            refs = record.get("evidence_refs")
            if not isinstance(refs, list) or any(ref not in indexed for ref in refs):
                errors.append(f"{prefix}.{dim}: evidence_refs must resolve")
                refs = []
            if record.get("state") == "NO":
                if not refs or any(not any(word in str(indexed[x].get("scope", "")).lower()
                                            for word in ("absent", "denied", "disabled", "not registered"))
                                   for x in refs):
                    errors.append(f"{prefix}.{dim}: NO requires scoped absence or denial evidence")
            if dim in {"registered", "runtime_discovered", "model_exposed", "invocation_authorized", "provider_responded"} and record.get("state") == "YES":
                if not refs or all(indexed[x].get("source") in {"ACTIVE_PROJECT_CONFIGURATION", "DIAGNOSTIC_GENERATION"} for x in refs):
                    errors.append(f"{prefix}.{dim}: static evidence cannot establish YES")
            if dim == "invocation_authorized" and record.get("state") == "YES":
                if not isinstance(op.get("invocation_arguments_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", op["invocation_arguments_sha256"]):
                    errors.append(f"{prefix}: authorization YES requires exact invocation argument digest")
                if not refs or any(indexed[x].get("scope") != "exact invocation arguments" for x in refs):
                    errors.append(f"{prefix}: authorization YES requires exact-argument evidence scope")
        ttl = op.get("source_ttl_seconds")
        if ttl is not None and (not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 0):
            errors.append(f"{prefix}: source_ttl_seconds must be a nonnegative integer or null")
        if ttl is None and op.get("freshness") != "UNKNOWN":
            errors.append(f"{prefix}: freshness must be UNKNOWN without an applicable TTL")
        if ttl is None and op.get("freshness_scope") != "NO_APPLICABLE_SOURCE_TTL":
            errors.append(f"{prefix}: freshness_scope must state that no applicable TTL exists")
        if op.get("generated_at") != report.get("generated_at"):
            errors.append(f"{prefix}: generated_at must match report generation time")
    return errors


def transition_status(previous, new, evidence=None):
    """Return an error string when a capability status transition lacks evidence."""
    evidence = evidence or {}
    next_status = {
        "CONFIGURED": {"DISCOVERED", "UNAVAILABLE"},
        "DISCOVERED": {"RESPONDED", "UNAVAILABLE"},
        "RESPONDED": {"QUALIFIED", "UNAVAILABLE"},
        "QUALIFIED": {"UNAVAILABLE"},
        "UNAVAILABLE": {"CONFIGURED"},
    }
    if not isinstance(previous, str) or not isinstance(new, str) or previous not in STATUSES or new not in STATUSES:
        return "unknown status"
    if new not in next_status[previous]:
        return f"invalid transition {previous}->{new}"
    if not isinstance(evidence, dict):
        return "transition evidence must be an object"
    required = {
        "DISCOVERED": "tool_metadata",
        "RESPONDED": "successful_call",
        "QUALIFIED": "qualification_record",
        "UNAVAILABLE": "failure_observation",
        "CONFIGURED": "retry_started",
    }[new]
    record = evidence.get(required)
    if not record:
        return f"{new} requires {required} evidence"
    if new in {"DISCOVERED", "RESPONDED"}:
        expected_key = "tool_identity" if new == "DISCOVERED" else None
        if not isinstance(record, dict):
            return f"{new} evidence must be an object"
        if not _timestamp(record.get("observed_at_utc")):
            return f"{new} requires a UTC observation timestamp"
        if new == "DISCOVERED" and not isinstance(record.get(expected_key), str):
            return "DISCOVERED requires an exact tool identity"
        if new == "RESPONDED" and (record.get("outcome") != "success" or not isinstance(record.get("tool_identity"), str)):
            return "RESPONDED requires a successful result and exact tool identity"
    if new == "QUALIFIED":
        successful_call = evidence.get("successful_call")
        if not isinstance(record, dict) or record.get("passed") is not True:
            return "QUALIFIED requires a passed qualification record"
        if not isinstance(successful_call, dict) or successful_call.get("outcome") != "success":
            return "QUALIFIED requires successful_call evidence"
    if new == "UNAVAILABLE" and (not isinstance(record, dict)
            or not _timestamp(record.get("observed_at_utc"))
            or not isinstance(record.get("reason"), str) or not record["reason"].strip()):
        return "UNAVAILABLE requires a timestamped failure reason"
    return None


def _timestamp(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == dt.timedelta(0)


def validate(config_path, inventory_path):
    errors = []
    if tomllib is None:
        return ["Python 3.11+ is required (tomllib missing)"]
    try:
        config = tomllib.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return [f"configuration: cannot parse ({type(exc).__name__})"]
    try:
        inventory = json.loads(pathlib.Path(inventory_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"inventory: cannot parse ({type(exc).__name__})"]

    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return ["configuration: mcp_servers must be a table"]
    if set(servers) != set(EXPECTED_ENDPOINTS):
        errors.append("configuration: provider set does not match the six expected providers")
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        return errors + ["inventory: schema_version 1 object required"]
    model = inventory.get("model")
    if not isinstance(model, dict) or model.get("offline_validation_is_not_live_verification") is not True:
        errors.append("inventory: must explicitly distinguish offline validation from live verification")
    providers = inventory.get("providers")
    if not isinstance(providers, list):
        return errors + ["inventory: providers must be an array"]
    indexed = {}
    for provider in providers:
        if not isinstance(provider, dict) or not isinstance(provider.get("id"), str):
            errors.append("inventory: each provider must have an id")
            continue
        name = provider["id"]
        if name in indexed:
            errors.append(f"{name}: duplicate provider")
        indexed[name] = provider
    if set(indexed) != set(EXPECTED_ENDPOINTS):
        errors.append("inventory: provider set does not match the six expected providers")

    for name, endpoint in EXPECTED_ENDPOINTS.items():
        entry = servers.get(name)
        cap = indexed.get(name)
        if not isinstance(entry, dict):
            errors.append(f"{name}: configured provider missing")
            continue
        if entry.get("url") != endpoint:
            errors.append(f"{name}: endpoint missing or changed")
        if entry.get("enabled", True) is not True:
            errors.append(f"{name}: provider must remain enabled")
        if cap is None:
            errors.append(f"{name}: capability inventory missing")
            continue
        if cap.get("endpoint") != endpoint:
            errors.append(f"{name}: inventory endpoint contradicts configuration")
        if not isinstance(cap.get("discovery_source"), str) or not cap["discovery_source"].strip():
            errors.append(f"{name}: discovery metadata source required")
        status = cap.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{name}: invalid provider status")
            status = None
        history = cap.get("status_history")
        if not isinstance(history, list) or not history or not all(isinstance(item, dict) for item in history):
            errors.append(f"{name}: status_history must be a nonempty array of evidence records")
        else:
            first = history[0]
            if first.get("status") != "CONFIGURED" or not isinstance(first.get("evidence"), dict) or not first["evidence"].get("configuration"):
                errors.append(f"{name}: status history must begin with configuration evidence")
            previous = "CONFIGURED"
            for item in history[1:]:
                evidence = item.get("evidence")
                error = transition_status(previous, item.get("status"), evidence)
                if error:
                    errors.append(f"{name}: invalid status history: {error}")
                    break
                previous = item["status"]
            if history[-1].get("status") != status:
                errors.append(f"{name}: current status contradicts status history")
        if status in {"RESPONDED", "QUALIFIED"} and not cap.get("responded_evidence"):
            errors.append(f"{name}: {status} requires successful-call evidence")
        if status == "QUALIFIED" and not cap.get("qualification_evidence"):
            errors.append(f"{name}: QUALIFIED requires qualification evidence")
        if status == "DISCOVERED":
            status_evidence = cap.get("status_evidence")
            if (not isinstance(status_evidence, dict)
                    or status_evidence.get("kind") != "tool_metadata_visible_in_codex_session"
                    or not _timestamp(status_evidence.get("observed_at_utc"))
                    or not isinstance(status_evidence.get("tool_identity"), str)):
                errors.append(f"{name}: DISCOVERED requires timestamped tool metadata evidence")
        if status == "UNAVAILABLE" and not cap.get("unavailable_evidence"):
            errors.append(f"{name}: UNAVAILABLE requires an observed failure reason")

        tools = cap.get("tools")
        if not isinstance(tools, list):
            errors.append(f"{name}: tools must be an array")
            continue
        tool_index = {}
        allowed = cap.get("automatic_research_allowlist")
        configured_allowed = entry.get("enabled_tools")
        if not isinstance(allowed, list) or any(not isinstance(t, str) for t in allowed):
            errors.append(f"{name}: automatic_research_allowlist must be a string array")
            continue
        if len(set(allowed)) != len(allowed):
            errors.append(f"{name}: duplicate allowlist entries")
        if configured_allowed != allowed:
            errors.append(f"{name}: config enabled_tools must exactly match the verified inventory allowlist")
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"].strip():
                errors.append(f"{name}: malformed tool metadata")
                continue
            tool_name = tool["name"]
            if tool.get("identity") != f"mcp__{name}__{tool_name}":
                errors.append(f"{name}.{tool_name}: exact discovered tool identity is missing or contradictory")
            if tool_name in tool_index:
                errors.append(f"{name}.{tool_name}: duplicate tool")
            tool_index[tool_name] = tool
            effect = tool.get("effect")
            if not isinstance(effect, str) or effect not in EFFECTS:
                errors.append(f"{name}.{tool_name}: invalid or missing effect classification")
                effect = None
            if not isinstance(tool.get("evidence"), str) or not tool["evidence"].strip():
                errors.append(f"{name}.{tool_name}: classification evidence required")
            category = tool.get("category")
            if not isinstance(category, str) or not category.strip():
                errors.append(f"{name}.{tool_name}: nonempty operation category required")
                category = ""
            eligible = effect == "READ_ONLY" and category not in PROHIBITED_CATEGORIES
            if not eligible and tool.get("automatic_research") is not False:
                errors.append(f"{name}.{tool_name}: prohibited or unknown-effect tool marked for automatic research")
            if tool.get("automatic_research") is not (tool_name in allowed):
                errors.append(f"{name}.{tool_name}: inventory eligibility contradicts provider allowlist")
        if status == "DISCOVERED" and isinstance(cap.get("status_evidence"), dict):
            identities = {tool.get("identity") for tool in tool_index.values()}
            if cap["status_evidence"].get("tool_identity") not in identities:
                errors.append(f"{name}: DISCOVERED evidence identity is absent from tool inventory")
        for tool_name in allowed:
            tool = tool_index.get(tool_name)
            if tool is None:
                errors.append(f"{name}.{tool_name}: allowlisted tool was not discovered")
            elif tool.get("effect") != "READ_ONLY" or tool.get("automatic_research") is not True:
                errors.append(f"{name}.{tool_name}: only verified, permitted READ_ONLY tools may be allowlisted")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[4] / ".codex" / "config.toml")
    parser.add_argument("inventory", nargs="?", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[4] / ".codex" / "provider-capabilities.json")
    parser.add_argument("--diagnostic", type=pathlib.Path,
                        help="validate an opt-in v2 diagnostic JSON report")
    args = parser.parse_args()
    if args.diagnostic:
        try:
            errors = validate_diagnostic(json.loads(args.diagnostic.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors = [f"diagnostic: cannot parse ({type(exc).__name__})"]
    else:
        errors = validate(args.config, args.inventory)
    for error in errors:
        print(f"ERROR: {error}")
    if not errors and not args.diagnostic:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        for provider in inventory["providers"]:
            print(f"{provider['id']}: {provider['status']} (live response unverified)")
    label = "offline v2 diagnostic validation" if args.diagnostic else "offline provider-policy validation"
    print(f"{'FAIL' if errors else 'PASS'}: {label}; live connectivity and qualification are unverified")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
