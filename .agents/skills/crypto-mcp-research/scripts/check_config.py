#!/usr/bin/env python3
"""Offline six-server TOML inventory, safe for secrets (URLs are not printed)."""
import argparse
import pathlib
import sys
try:
    import tomllib
except ImportError:
    print("Requires Python 3.11+ (tomllib)"); sys.exit(2)
EXPECTED = ("coingecko", "coinpaprika", "dexpaprika", "cryptocom_market", "hive", "gdelt")
def check(path):
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as e:
        return [f"Cannot parse TOML: {type(e).__name__}"]
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return ["mcp_servers must be a table"]
    problems = []
    for name in EXPECTED:
        entry = servers.get(name)
        if not isinstance(entry, dict):
            problems.append(f"{name}: absent"); continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            problems.append(f"{name}: expected HTTPS URL")
        if entry.get("enabled", True) is not True:
            problems.append(f"{name}: not enabled")
        print(f"{name}: {'CONFIGURED' if isinstance(url,str) and url.startswith('https://') else 'INVALID'} (connectivity untested)")
    return problems
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=pathlib.Path)
    args = parser.parse_args()
    errors = check(args.config)
    for error in errors: print(f"ERROR: {error}")
    print(f"{'PASS' if not errors else 'FAIL'}: local configuration only; live functionality UNVERIFIED")
    sys.exit(bool(errors))
