"""Read-only SQLite validation and local JSON export for proposal records."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from proposal_workflow import ProposalError, check_schema, validate_persisted_record


_SELECT_COLUMNS = ("proposal_id", "details_json", "details_digest", "state", "external_attempt_status", "created_at_utc", "updated_at_utc", "approval_metadata_json", "operation_json", "response_json")


def _readonly_connect(database: str | Path) -> sqlite3.Connection:
    path = Path(database).expanduser().resolve()
    uri = path.as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    db.execute("PRAGMA query_only=ON")
    return db


def _exclusive_write(path: Path, payload: bytes) -> str:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() == payload:
            return "already exported"
        raise ProposalError(f"conflicting artifact exists: {path}")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return "exported"


def export_proposals(database: str | Path, output_dir: str | Path) -> int:
    try:
        db = _readonly_connect(database)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"FAIL: cannot open proposal database read-only: {exc}", file=sys.stderr)
        return 1
    try:
        db.execute("BEGIN")
        try:
            check_schema(db)
        except ProposalError as exc:
            raise ProposalError(f"proposal schema missing or incompatible; export will not initialize or migrate it ({exc})") from exc
        selected = ",".join(_SELECT_COLUMNS)
        raw_rows = db.execute(f"SELECT {selected} FROM proposals ORDER BY proposal_id").fetchall()
        db.execute("COMMIT")
    except (sqlite3.Error, ProposalError) as exc:
        if db.in_transaction:
            db.execute("ROLLBACK")
        print(f"FAIL: proposal schema or read error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    try:
        artifacts = []
        for raw in raw_rows:
            row = dict(zip(_SELECT_COLUMNS, raw))
            artifact = validate_persisted_record(row)
            target_name = artifact["proposal_id"] + ".json"
            payload = (json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            artifacts.append((target_name, payload))

        outdir = Path(output_dir).expanduser()
        existing_names = [entry.name for entry in outdir.iterdir()] if outdir.exists() else []
        for name, payload in artifacts:
            collisions = [existing_name for existing_name in existing_names if existing_name.casefold() == name.casefold() and existing_name != name]
            if collisions:
                raise ProposalError(f"case-insensitive export-path collision: {collisions[0]} vs {name}")
            target = outdir / name
            if target.is_symlink():
                raise ProposalError(f"export-path collision with symlink: {target}")
            if target.exists() and target.read_bytes() != payload:
                raise ProposalError(f"conflicting artifact exists: {target}")

        outdir.mkdir(parents=True, exist_ok=True)
        for name, payload in artifacts:
            print(f"{name}: {_exclusive_write(outdir / name, payload)}")
    except (OSError, ProposalError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="paper-testing/runs/proposals.sqlite3")
    parser.add_argument("--output", default="paper-testing/proposals")
    args = parser.parse_args()
    return export_proposals(args.database, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
