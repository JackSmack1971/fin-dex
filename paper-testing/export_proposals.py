"""Export immutable local proposal records; never reads or writes the paper-order journal."""
import argparse
import json
from pathlib import Path
import sys

from proposal_workflow import ProposalStore, digest, validate_details


def export_proposals(database: str | Path, output_dir: str | Path) -> int:
    store = ProposalStore(database)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with store._db() as db:
        rows = db.execute("SELECT proposal_id,details_json,details_digest,state,external_attempt_status,created_at_utc,updated_at_utc FROM proposals ORDER BY proposal_id").fetchall()
    try:
        for proposal_id, details_json, details_digest, state, attempt, created, updated in rows:
            details = json.loads(details_json)
            if digest(details) != details_digest: raise ValueError(f"{proposal_id}: details digest mismatch")
            if state == "VALIDATED": validate_details(details)
            artifact = {"schema_version":"proposal.v1", "proposal_id":proposal_id, "details":details,
                        "details_digest":details_digest, "state":state, "external_attempt_status":attempt,
                        "created_at_utc":created, "updated_at_utc":updated}
            target = out / f"{proposal_id}.json"
            payload = json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            try:
                with target.open("x", encoding="utf-8", newline="\n") as f: f.write(payload)
            except FileExistsError:
                if target.read_text(encoding="utf-8") != payload: raise ValueError(f"{proposal_id}: conflicting existing export")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="paper-testing/runs/proposals.sqlite3")
    parser.add_argument("--output", default="paper-testing/proposals")
    args = parser.parse_args()
    return export_proposals(args.database, args.output)


if __name__ == "__main__": raise SystemExit(main())
