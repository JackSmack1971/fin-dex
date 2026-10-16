#!/usr/bin/env python3
"""Offline structural verification of research claim ledgers; no API calls."""
import argparse
import datetime as dt
import json
import pathlib
import sys

STATUSES = {"CORROBORATED", "SINGLE_SOURCE", "CONFLICTED", "UNVERIFIED"}
REQUIRED_OBS = ("provider", "tool", "asset_id", "metric", "value", "unit", "observed_at_utc", "retrieved_at_utc")

def utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("must be UTC ISO timestamp ending Z")
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != dt.timedelta(0):
        raise ValueError("must be UTC")
    return result

def validate(data):
    errors = []
    if not isinstance(data, dict):
        return ["root must be an object"]
    try:
        utc(data.get("as_of_utc"))
    except (TypeError, ValueError) as exc:
        errors.append(f"as_of_utc: {exc}")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return errors + ["claims must be a nonempty array"]
    ids = set()
    for i, claim in enumerate(claims):
        p = f"claims[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{p}: expected object"); continue
        ident = claim.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            errors.append(f"{p}.id: missing or duplicate")
        else:
            ids.add(ident)
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
        if status == "CORROBORATED" and len({o.get('provider') for o in observations if isinstance(o, dict)}) < 2:
            errors.append(f"{p}: CORROBORATED requires at least two provider records (independence still manual)")
        for j, obs in enumerate(observations):
            q = f"{p}.observations[{j}]"
            if not isinstance(obs, dict):
                errors.append(f"{q}: expected object"); continue
            for k in REQUIRED_OBS:
                if k not in obs or obs[k] is None or obs[k] == "":
                    errors.append(f"{q}.{k}: required")
            for k in ("observed_at_utc", "retrieved_at_utc"):
                if obs.get(k):
                    try: utc(obs[k])
                    except (TypeError, ValueError) as exc: errors.append(f"{q}.{k}: {exc}")
            if "value" in obs and (isinstance(obs["value"], (dict, list)) or isinstance(obs["value"], bool)):
                errors.append(f"{q}.value: must be scalar, not container or boolean")
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=pathlib.Path)
    args = parser.parse_args()
    try: data = json.loads(args.ledger.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read JSON: {exc}"); return 2
    errors = validate(data)
    for error in errors: print(f"ERROR: {error}")
    print(f"{'FAIL' if errors else 'PASS'}: {len(errors)} structural error(s); factual truth and independence NOT verified")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
