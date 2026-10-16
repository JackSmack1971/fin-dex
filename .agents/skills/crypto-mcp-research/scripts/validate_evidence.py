#!/usr/bin/env python3
"""Offline structural verification of research claim ledgers; no API calls."""
import argparse
import datetime as dt
import json
import math
import pathlib
import sys

STATUSES = {"CORROBORATED", "SINGLE_SOURCE", "CONFLICTED", "UNVERIFIED", "UNAVAILABLE"}
REQUIRED_OBS = ("provider", "tool", "asset_id", "metric", "value", "unit", "observed_at_utc", "retrieved_at_utc")

def utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("must be UTC ISO timestamp ending Z")
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("must include UTC timezone")
    if result.utcoffset() != dt.timedelta(0):
        raise ValueError("must be UTC")
    return result

def validate_with_warnings(data):
    errors = []
    warnings = []
    if not isinstance(data, dict):
        return ["root must be an object"], warnings
    try:
        utc(data.get("as_of_utc"))
    except (TypeError, ValueError) as exc:
        errors.append(f"as_of_utc: {exc}")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return errors + ["claims must be a nonempty array"], warnings
    ids = set()
    as_of = None
    try:
        as_of = utc(data.get("as_of_utc"))
    except (TypeError, ValueError):
        pass
    for i, claim in enumerate(claims):
        p = f"claims[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{p}: expected object"); continue
        ident = claim.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident.strip() in ids:
            errors.append(f"{p}.id: missing or duplicate")
        else:
            ids.add(ident.strip())
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            errors.append(f"{p}.statement: nonempty string required")
        status = claim.get("status")
        if status not in STATUSES:
            errors.append(f"{p}.status: invalid")
        observations = claim.get("observations", [])
        if not isinstance(observations, list):
            errors.append(f"{p}.observations: expected array"); continue
        if status in {"CORROBORATED", "SINGLE_SOURCE"} and not observations:
            errors.append(f"{p}: supported claim requires observations")
        if status == "CORROBORATED" and len({o.get('provider') for o in observations if isinstance(o, dict) and isinstance(o.get('provider'), str)}) < 2:
            errors.append(f"{p}: CORROBORATED requires at least two provider records (independence still manual)")
        seen = set()
        parsed = []
        for j, obs in enumerate(observations):
            q = f"{p}.observations[{j}]"
            if not isinstance(obs, dict):
                errors.append(f"{q}: expected object"); continue
            for k in REQUIRED_OBS:
                if k not in obs or obs[k] is None or (isinstance(obs[k], str) and not obs[k].strip()):
                    errors.append(f"{q}.{k}: required")
            for k in ("provider", "tool", "asset_id", "metric", "unit"):
                if k in obs and (not isinstance(obs[k], str) or not obs[k].strip()):
                    errors.append(f"{q}.{k}: must be a nonempty string")
            for k in ("observed_at_utc", "retrieved_at_utc"):
                if obs.get(k):
                    try:
                        value = utc(obs[k]); parsed.append((j, k, value))
                    except (TypeError, ValueError) as exc: errors.append(f"{q}.{k}: {exc}")
            value = obs.get("value")
            if isinstance(value, bool) or isinstance(value, (dict, list)):
                errors.append(f"{q}.value: must be scalar, not container or boolean")
            elif isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{q}.value: numeric value must be finite")
            window_bounds = {}
            for k in ("window_start_utc", "window_end_utc"):
                if k in obs:
                    try: window_bounds[k] = utc(obs[k])
                    except (TypeError, ValueError) as exc: errors.append(f"{q}.{k}: {exc}")
            if ("window_start_utc" in obs) != ("window_end_utc" in obs):
                errors.append(f"{q}: window_start_utc and window_end_utc must be supplied together")
            elif len(window_bounds) == 2 and window_bounds["window_start_utc"] > window_bounds["window_end_utc"]:
                errors.append(f"{q}: observation window start is after end")
            # Identity key intentionally includes source and both time anchors, so
            # repeated copies of the same observation cannot inflate support.
            if all(isinstance(obs.get(k), str) and obs[k].strip() for k in ("provider", "tool", "asset_id", "metric", "observed_at_utc", "retrieved_at_utc")):
                key = tuple(obs[k].strip() for k in ("provider", "tool", "asset_id", "metric", "observed_at_utc", "retrieved_at_utc"))
                if key in seen: errors.append(f"{q}: duplicate observation identity within claim")
                seen.add(key)
        times = {j: {} for j in range(len(observations))}
        for j, k, value in parsed: times[j][k] = value
        for j, anchors in times.items():
            q = f"{p}.observations[{j}]"
            observed, retrieved = anchors.get("observed_at_utc"), anchors.get("retrieved_at_utc")
            if observed and retrieved and observed > retrieved:
                errors.append(f"{q}: observed_at_utc is after retrieved_at_utc")
            if retrieved and as_of and retrieved > as_of:
                errors.append(f"{q}: retrieved_at_utc is after as_of_utc")
        if status == "CORROBORATED" and len(observations) > 1:
            valid_obs = [o for o in observations if isinstance(o, dict)]
            # Token-contract and on-chain identity claims require chain identity;
            # market prices and exchange instruments do not universally do so.
            if claim.get("claim_type") in {"TOKEN_CONTRACT_IDENTITY", "ON_CHAIN_IDENTITY"}:
                chain_ids = [o.get("chain_id") for o in valid_obs]
                if not all(isinstance(x, str) and x.strip() for x in chain_ids):
                    errors.append(f"{p}: CORROBORATED identity claim requires explicit chain-qualified observations")
                elif len(set(chain_ids)) != 1:
                    errors.append(f"{p}: CORROBORATED observations have incompatible chain_id")
            origins_required = [o.get("upstream_source") for o in valid_obs]
            methods_required = [o.get("measurement_method") for o in valid_obs]
            if not all(isinstance(x, str) and x.strip() for x in origins_required):
                errors.append(f"{p}: CORROBORATED requires documented upstream_source for every observation")
            elif len(set(origins_required)) != len(origins_required):
                errors.append(f"{p}: CORROBORATED observations do not establish distinct upstream sources")
            if not all(isinstance(x, str) and x.strip() for x in methods_required):
                errors.append(f"{p}: CORROBORATED requires documented measurement_method for every observation")
            for field in ("asset_id", "metric", "unit"):
                values = {o.get(field) for o in valid_obs if isinstance(o.get(field), str) and o.get(field).strip()}
                if len(values) > 1: errors.append(f"{p}: CORROBORATED observations have incompatible {field}")
                elif len(values) == 1 and len(valid_obs) > 1 and any(not o.get(field) for o in valid_obs):
                    warnings.append(f"{p}: {field} compatibility is unverifiable for one or more observations")
            windows = [o for o in valid_obs if "window_start_utc" in o and "window_end_utc" in o]
            if len(windows) > 1 and len({(o["window_start_utc"], o["window_end_utc"]) for o in windows}) > 1:
                errors.append(f"{p}: CORROBORATED observations have incompatible windows")
            elif len(windows) != len(valid_obs):
                warnings.append(f"{p}: observation-window compatibility is unverifiable for one or more observations")
            warnings.append(f"{p}: provider count and provenance metadata do not prove source independence or factual truth")
    return errors, warnings

def validate(data):
    return validate_with_warnings(data)[0]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=pathlib.Path)
    args = parser.parse_args()
    try: data = json.loads(args.ledger.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read JSON: {exc}"); return 2
    errors, warnings = validate_with_warnings(data)
    for error in errors: print(f"ERROR: {error}")
    for warning in warnings: print(f"WARNING: {warning}")
    print(f"{'FAIL' if errors else 'PASS'}: {len(errors)} structural error(s); factual truth and independence NOT verified")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
