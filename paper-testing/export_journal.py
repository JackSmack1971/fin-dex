#!/usr/bin/env python3
"""Export validated SQLite paper intents as immutable, provenance-bearing artifacts."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, re, sqlite3, sys
import jsonschema
import datetime as dt
from decimal import Decimal, InvalidOperation

from validate_paper_record import validate_signal, validate_run

ROOT = pathlib.Path(__file__).resolve().parent
REQUIRED_TOOLS = {"get_paper_status", "get_instrument_metadata", "submit_simulated_order", "reconcile_order"}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

def digest(value): return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
def client_order_id(signal_id, request_digest):
    token = hashlib.sha256(f"{signal_id}\0{request_digest}".encode()).hexdigest()[:32]
    return f"fdx-{token}"

def parse_json(raw, label):
    if not isinstance(raw, str): raise ValueError(f"{label} missing")
    return json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"invalid number {x}")))

def validate_evidence_pair(value, label):
    if not isinstance(value, dict): raise ValueError(f"{label} must be an object")
    observed, retrieved = value.get("observed_at_utc"), value.get("retrieved_at_utc")
    try:
        if not isinstance(observed, str) or not observed.endswith("Z") or not isinstance(retrieved, str) or not retrieved.endswith("Z"): raise ValueError()
        obs = dt.datetime.fromisoformat(observed[:-1] + "+00:00")
        ret = dt.datetime.fromisoformat(retrieved[:-1] + "+00:00")
        if obs.utcoffset() != dt.timedelta(0) or ret.utcoffset() != dt.timedelta(0) or ret < obs: raise ValueError()
    except (TypeError, ValueError): raise ValueError(f"{label} timestamps must be valid UTC Z values in observation/retrieval order")

def validate_mode_evidence(value, schemas, label):
    validate_evidence_pair(value, label)
    if value.get("mode") != "SIMULATED": raise ValueError(f"{label} is not SIMULATED")
    if not isinstance(value.get("account_id"), str) or not value["account_id"].strip(): raise ValueError(f"{label} account identity missing")
    if value.get("tool_schema_digest") != digest(schemas): raise ValueError(f"{label} tool schema digest mismatch")

def validate_metadata(value, signal, label):
    if not isinstance(value, dict): raise ValueError(f"{label} must be an object")
    validate_evidence_pair(value, label)
    keys = ("venue", "instrument_id", "base", "quote", "instrument_type")
    if any(value.get(k) != signal["asset"].get(k) for k in keys): raise ValueError(f"{label} instrument identity differs from signal")
    for key in ("min_quantity", "max_quantity", "quantity_step", "price_tick"):
        try:
            number = Decimal(str(value[key]))
            if not number.is_finite() or number <= 0: raise ValueError()
        except (KeyError, InvalidOperation, ValueError, TypeError): raise ValueError(f"{label}.{key} must be finite and positive")
    minimum, maximum, step = (Decimal(str(value[k])) for k in ("min_quantity", "max_quantity", "quantity_step"))
    quantity = Decimal(str(signal["order"]["quantity"]))
    if maximum < minimum or quantity < minimum or quantity > maximum or quantity % step != 0: raise ValueError(f"{label} quantity constraints do not support signal order")
    if signal["order"]["order_type"] == "limit" and Decimal(str(signal["order"]["limit_price"])) % Decimal(str(value["price_tick"])) != 0:
        raise ValueError(f"{label} price tick does not support signal order")

def build_export(row):
    if not re.fullmatch(r"fdx-[a-f0-9]{32}", row["client_order_id"]): raise ValueError("invalid client order ID artifact name")
    try:
        created = dt.datetime.fromisoformat(row["created_at_utc"][:-1] + "+00:00")
        if not row["created_at_utc"].endswith("Z") or created.utcoffset() != dt.timedelta(0): raise ValueError()
    except (TypeError, ValueError): raise ValueError("journal creation timestamp is not UTC ISO-8601 Z")
    intent = parse_json(row["intent_json"], "intent_json")
    required = {"signal", "venue_evidence", "instrument_metadata", "tool_schemas", "request", "request_digest"}
    if not isinstance(intent, dict) or not required <= intent.keys(): raise ValueError("intent evidence incomplete")
    signal = intent["signal"]; validate_signal(signal)
    if signal["signal_id"] != row["signal_id"]: raise ValueError("journal signal identity mismatch")
    request = intent["request"]
    if intent["request_digest"] != row["request_digest"] or digest(request) != row["request_digest"]: raise ValueError("request digest mismatch")
    meta = intent["instrument_metadata"]
    schemas = intent["tool_schemas"]
    evidence = intent["venue_evidence"]
    dispatch_present = "dispatch_check" in intent
    dispatch = intent.get("dispatch_check")
    if not isinstance(schemas, dict) or set(schemas) != REQUIRED_TOOLS: raise ValueError("tool schema snapshot does not contain the exact supported operations")
    validate_mode_evidence(evidence, schemas, "venue evidence")
    validate_metadata(meta, signal, "instrument metadata")
    instrument_contract = {k:v for k,v in meta.items() if k not in {"observed_at_utc", "retrieved_at_utc"}}
    expected = {"account_id": evidence.get("account_id"), "asset": signal["asset"], "order": signal["order"], "signal_id": signal["signal_id"],
                "signal_digest": digest(signal), "instrument_metadata_digest": digest(instrument_contract), "tool_schema_digest": evidence.get("tool_schema_digest")}
    if request != expected: raise ValueError("request cross-field consistency failure")
    if dispatch_present:
        if not isinstance(dispatch, dict) or not {"venue_evidence", "instrument_metadata", "tool_schemas"} <= dispatch.keys(): raise ValueError("dispatch evidence incomplete")
        de, dm, ds = dispatch["venue_evidence"], dispatch["instrument_metadata"], dispatch["tool_schemas"]
        validate_mode_evidence(de, schemas, "dispatch venue evidence")
        validate_metadata(dm, signal, "dispatch instrument metadata")
        if de.get("account_id") != request["account_id"]: raise ValueError("dispatch account identity mismatch")
        if not isinstance(ds, dict) or ds != schemas or digest(ds) != request["tool_schema_digest"] or de.get("tool_schema_digest") != request["tool_schema_digest"]: raise ValueError("dispatch tool schema mismatch")
        dispatch_contract = {k:v for k,v in dm.items() if k not in {"observed_at_utc", "retrieved_at_utc"}} if isinstance(dm, dict) else None
        if dispatch_contract != instrument_contract: raise ValueError("dispatch instrument metadata mismatch")
    elif row["state"] == "UNKNOWN": raise ValueError("UNKNOWN state lacks dispatch evidence")
    if row["client_order_id"] != client_order_id(row["signal_id"], row["request_digest"]): raise ValueError("client order id mismatch")
    receipt = parse_json(row["receipt_json"], "receipt_json") if row["receipt_json"] else None
    if row["state"] == "ACKNOWLEDGED":
        if canonical(receipt) != row["receipt_json"]: raise ValueError("stored receipt is not canonical JSON")
        if not receipt or receipt.get("outcome") != "ACKNOWLEDGED" or receipt.get("request_digest") != row["request_digest"] or receipt.get("client_order_id") != row["client_order_id"]: raise ValueError("acknowledgement does not match intent")
        receipt_digest = hashlib.sha256(row["receipt_json"].encode("utf-8")).hexdigest()
        if row.get("receipt_digest") and row["receipt_digest"] != receipt_digest: raise ValueError("anchored receipt digest mismatch")
        receipt_status = "ANCHORED_IN_JOURNAL" if row.get("receipt_digest") else "COMPUTED_AT_EXPORT_LEGACY"
        status = "ACKNOWLEDGED"
    elif row["state"] in {"RESERVED", "UNKNOWN"}:
        if receipt is not None: raise ValueError("unacknowledged state contains a receipt")
        if row["state"] == "UNKNOWN" and not dispatch_present: raise ValueError("UNKNOWN state lacks dispatch evidence")
        status = "SUBMISSION_UNKNOWN" if dispatch_present else "PREPARED"
        receipt_digest = None; receipt_status = "NOT_PRESENT"
    else: raise ValueError("invalid journal lifecycle state")
    rec = parse_json(row["reconciliation_json"], "reconciliation_json") if row["reconciliation_json"] else {k:None for k in ("order_id", "fills", "fees", "funding", "account_equity", "equity_history")}
    if not isinstance(rec, dict): raise ValueError("reconciliation must be object")
    if rec and not receipt and any(v is not None for v in rec.values()): raise ValueError("reconciliation exists without an acknowledged receipt")
    rec = {k:rec.get(k) for k in ("order_id", "fills", "fees", "funding", "account_equity", "equity_history")}
    submission = receipt if receipt is not None else ({"outcome":"UNKNOWN", "client_order_id":row["client_order_id"], "request_digest":row["request_digest"]} if status == "SUBMISSION_UNKNOWN" else None)
    if status == "PREPARED": ev = None
    else: ev = evidence
    if status == "ACKNOWLEDGED" and rec["order_id"] and rec["order_id"] == receipt.get("order_id"): status = "RECONCILED"
    missing = [k for k,v in rec.items() if v is None]
    run = {"schema_version":"paper-run.v1", "run_id":row["signal_id"], "status":status, "signal":signal, "venue_evidence":ev,
           "submission":submission, "reconciliation":rec, "evaluation":{"complete":False,"missing_fields":missing,"metrics":None}}
    validate_run(run)
    run["export_version"] = "paper-journal-export.v1"
    run["provenance"] = {"journal_created_at_utc":{"value":row["created_at_utc"],"source":"paper_intents.created_at_utc"},
      "signal":{"source":"intent_json.signal"},"request":{"source":"intent_json.request","integrity":"sha256 verified against paper_intents.request_digest"},
      "instrument_metadata":{"source":"intent_json.instrument_metadata"},"account_identity":{"source":"intent_json.venue_evidence.account_id"},
      "tool_schema_snapshot":{"source":"intent_json.tool_schemas"},"dispatch_evidence":{"source":"intent_json.dispatch_check","present":dispatch_present},
      "receipt":{"source":"receipt_json","digest":receipt_digest,"digest_status":receipt_status,"hash_claim":"local integrity check; not a Liquid attestation"},
      "reconciliation":{"source":"reconciliation_json"}}
    jsonschema.validate(run, json.loads((ROOT / "schemas" / "journal-export-v1.schema.json").read_text(encoding="utf-8")))
    return run

def _exclusive_write(path, payload):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() == payload: return "already exported"
        raise ValueError(f"conflicting artifact exists: {path}")
    try:
        with os.fdopen(fd, "wb") as stream: stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        try: path.unlink()
        except OSError: pass
        raise
    return "exported"

def export(database, outdir):
    database = pathlib.Path(database).expanduser().resolve()
    uri = database.as_uri() + "?mode=ro"
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        print(f"FAIL: cannot open journal read-only: {exc}", file=sys.stderr)
        return 1
    try:
        columns = {r[1] for r in db.execute("PRAGMA table_info(paper_intents)")}
        required = {"signal_id", "request_digest", "client_order_id", "state", "created_at_utc", "receipt_json", "intent_json", "reconciliation_json"}
        if not required <= columns: raise ValueError(f"journal schema missing required columns: {', '.join(sorted(required - columns))}")
        receipt_column = "receipt_digest" if "receipt_digest" in columns else "NULL AS receipt_digest"
        rows = db.execute(f"SELECT signal_id,request_digest,client_order_id,state,created_at_utc,receipt_json,intent_json,reconciliation_json,{receipt_column} FROM paper_intents ORDER BY created_at_utc, client_order_id").fetchall()
    except (sqlite3.Error, ValueError) as exc:
        print(f"FAIL: journal schema or read error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    errors=[]
    for raw in rows:
        row=dict(zip(("signal_id","request_digest","client_order_id","state","created_at_utc","receipt_json","intent_json","reconciliation_json","receipt_digest"),raw))
        try:
            artifact=build_export(row)
            payload=(canonical(artifact)+"\n").encode()
            print(f"{row['client_order_id']}: {_exclusive_write(outdir / (row['client_order_id'] + '.json'), payload)}")
        except Exception as exc:
            errors.append((row["signal_id"],str(exc)))
            print(f"{row['signal_id']}: INVALID: {exc}",file=sys.stderr)
    return 1 if errors else 0

def main():
    p=argparse.ArgumentParser(); p.add_argument("--database",default=str(ROOT/"runs"/"paper-idempotency.sqlite3")); p.add_argument("--outdir",default=str(ROOT/"runs")); a=p.parse_args()
    try: return export(a.database,a.outdir)
    except Exception as exc: print(f"FAIL: {exc}",file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main())
