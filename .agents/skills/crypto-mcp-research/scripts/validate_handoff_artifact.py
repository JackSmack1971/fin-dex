#!/usr/bin/env python3
"""Validate a saved handoff artifact and report persistence separately from lifecycle."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from validate_handoffs import validate_handoff


def validate_artifact(path):
    errors = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"artifact persistence failed: cannot read saved artifact: {exc}"]
    if not raw:
        return ["artifact persistence failed: saved artifact is empty"]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return [f"artifact persistence failed: saved artifact is malformed JSON: {exc}"]
    errors.extend(f"saved handoff is nonconforming: {e}" for e in validate_handoff(doc))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    args = parser.parse_args()
    errors = validate_artifact(args.artifact)
    for error in errors:
        print("ERROR:", error)
    if errors:
        print("GENERATION: NOT_EVALUATED")
        print("PERSISTENCE: FAIL")
        print("SCHEMA_VALIDATION: FAIL")
        print("LIFECYCLE_ACCEPTANCE: NOT_EVALUATED")
        return 1
    print("GENERATION: NOT_EVALUATED")
    print("PERSISTENCE: PASS (saved file exists and is nonempty)")
    print("SCHEMA_VALIDATION: PASS")
    print("LIFECYCLE_ACCEPTANCE: NOT_EVALUATED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
