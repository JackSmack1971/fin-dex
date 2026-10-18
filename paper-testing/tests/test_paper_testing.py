import hashlib
import json
import multiprocessing
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapter import AdapterRefusal, PaperAdapter, evaluation_completeness
from validate_paper_record import validate_run, validate_signal
from export_journal import export as export_journal

OPS = {
    "get_paper_status": {"name": "get_paper_status", "input": {"type": "object"}},
    "get_instrument_metadata": {"name": "get_instrument_metadata", "input": {"type": "object", "required": ["instrument_id"]}},
    "submit_simulated_order": {"name": "submit_simulated_order", "input": {"type": "object", "required": ["client_order_id", "order"]}},
    "reconcile_order": {"name": "reconcile_order", "input": {"type": "object", "required": ["client_order_id"]}},
}

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def signal(gate="QUALIFIED", direction="long", side="buy", quantity=0.02, order_type="market", price=None):
    order = {"side": side, "order_type": order_type, "quantity": quantity, "time_in_force": "IOC"}
    if price is not None: order["limit_price"] = price
    return {"schema_version": "signal.v1", "signal_id": "s-1", "decision_time_utc": now(),
            "asset": {"venue": "Liquid", "instrument_id": "BTC-PERP", "base": "BTC", "quote": "USD", "instrument_type": "perpetual"},
            "direction": direction, "horizon_days": 14, "rationale": "fixture", "evidence_refs": ["run:abc"], "gate_result": gate, "order": order}

def _competing_reservation(db_path, start, results):
    from idempotency import DuplicateIntent, ReservationStore
    start.wait(10)
    try:
        ReservationStore(db_path).reserve("process-race", "b" * 64, now(), "{}")
        results.put("reserved")
    except DuplicateIntent:
        results.put("blocked")

class FakeVenue:
    def __init__(self, mode="SIMULATED", account="paper-1", fail=False):
        self.mode, self.account, self.fail, self.submissions = mode, account, fail, 0
        self.lock = threading.Lock()
        self.schemas = json.loads(json.dumps(OPS))
    def get_tool_schemas(self): return self.schemas
    def get_paper_status(self):
        timestamp = now()
        digest = hashlib.sha256(json.dumps(self.schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"mode": self.mode, "account_id": self.account, "observed_at_utc": timestamp, "retrieved_at_utc": timestamp, "tool_schema_digest": digest}
    def get_instrument_metadata(self, instrument_id):
        timestamp = now()
        return {"venue": "Liquid", "instrument_id": instrument_id, "base": "BTC", "quote": "USD", "instrument_type": "perpetual",
                "min_quantity": "0.001", "max_quantity": "5", "quantity_step": "0.001", "price_tick": "0.1",
                "observed_at_utc": timestamp, "retrieved_at_utc": timestamp}
    def submit_simulated_order(self, request):
        with self.lock: self.submissions += 1
        if self.fail: raise TimeoutError("uncertain")
        return {"order_id": "o-1", "acknowledged": True}
    def reconcile_order(self, client_order_id): return {"order_id": None, "fills": None}

class PaperTestingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.temp.name) / "idempotency.sqlite3"
        import adapter
        self.registry_patch = patch.object(adapter, "_default_registry_path", return_value=self.db)
        self.registry_patch.start()
        self.venue = FakeVenue()
    def tearDown(self):
        self.registry_patch.stop()
        self.temp.cleanup()
    def adapter(self, venue=None): return PaperAdapter(venue or self.venue, OPS, "paper-1")

    def test_adapter_rejects_caller_selected_registry_path(self):
        with self.assertRaises(TypeError): PaperAdapter(self.venue, OPS, "paper-1", self.db)
        instance = self.adapter()
        with self.assertRaises(AttributeError): instance.reservation_store = object()
        with self.assertRaises(AttributeError): instance._reservation_store = object()
        with self.assertRaises(AttributeError): instance.reservation_store.path = self.db.parent / "alternate.sqlite3"
        with self.assertRaises(AttributeError): instance.reservation_store._path = self.db.parent / "alternate.sqlite3"

    def test_valid_and_rejected_signals_and_intent_consistency(self):
        self.assertTrue(validate_signal(signal()))
        self.assertTrue(validate_signal(signal("REJECTED")))
        bad = signal(); bad["unexpected"] = True
        with self.assertRaises(ValueError): validate_signal(bad)
        with self.assertRaises(ValueError): validate_signal(signal(direction="long", side="sell"))

    def test_runtime_tool_schema_drift_and_account_mismatch_fail_closed(self):
        changed = FakeVenue(); changed.schemas["submit_simulated_order"]["input"]["required"].append("mode")
        with self.assertRaises(AdapterRefusal): self.adapter(changed).submit_once(signal())
        wrong_account = FakeVenue(account="different-paper")
        with self.assertRaises(AdapterRefusal): self.adapter(wrong_account).submit_once(signal())

    def test_live_or_stale_mode_and_instrument_evidence_rejected(self):
        with self.assertRaises(AdapterRefusal): self.adapter(FakeVenue("LIVE")).submit_once(signal())
        class Stale(FakeVenue):
            def get_paper_status(self):
                result = super().get_paper_status(); result["retrieved_at_utc"] = "2000-01-01T00:00:00Z"; return result
        with self.assertRaises(AdapterRefusal): self.adapter(Stale()).submit_once(signal())
        class WrongInstrument(FakeVenue):
            def get_instrument_metadata(self, instrument_id):
                result = super().get_instrument_metadata(instrument_id); result["base"] = "ETH"; return result
        with self.assertRaises(AdapterRefusal): self.adapter(WrongInstrument()).submit_once(signal())

    def test_mode_change_after_reservation_blocks_dispatch_permanently(self):
        class ChangesMode(FakeVenue):
            def __init__(self):
                super().__init__(); self.status_reads = 0
            def get_paper_status(self):
                self.status_reads += 1
                if self.status_reads == 2:
                    self.mode = "LIVE"
                elif self.status_reads > 2:
                    self.mode = "SIMULATED"
                return super().get_paper_status()
        venue = ChangesMode()
        adapter = self.adapter(venue)
        with self.assertRaisesRegex(AdapterRefusal, "DISPATCH_BLOCKED"):
            adapter.submit_once(signal())
        self.assertEqual(venue.submissions, 0)
        self.assertEqual(adapter.reservation_store.get("s-1")["state"], "RESERVED")
        venue.mode = "SIMULATED"
        with self.assertRaisesRegex(AdapterRefusal, "already reserved"):
            self.adapter(venue).submit_once(signal())

    def test_registry_lock_after_fresh_check_fails_fast_without_dispatch(self):
        class LocksRegistryOnDispatchCheck(FakeVenue):
            def __init__(self, path):
                super().__init__(); self.path = path; self.status_reads = 0; self.lock_connection = None
            def get_paper_status(self):
                self.status_reads += 1
                if self.status_reads == 2:
                    self.lock_connection = sqlite3.connect(self.path, isolation_level=None)
                    self.lock_connection.execute("BEGIN IMMEDIATE")
                return super().get_paper_status()
        venue = LocksRegistryOnDispatchCheck(self.db)
        adapter = self.adapter(venue)
        with self.assertRaisesRegex(AdapterRefusal, "DISPATCH_BLOCKED"):
            adapter.submit_once(signal())
        self.assertEqual(venue.submissions, 0)
        self.assertEqual(adapter.reservation_store.get("s-1")["state"], "RESERVED")
        venue.lock_connection.execute("ROLLBACK")
        venue.lock_connection.close()

    def test_caller_cannot_supply_forged_status_evidence(self):
        with self.assertRaises(TypeError): self.adapter().submit_once(signal(), {"mode": "SIMULATED", "account_id": "paper-1"})

    def test_rejected_malformed_nonfinite_and_precision_invalid_orders_blocked(self):
        adapter = self.adapter()
        cases = (signal("REJECTED"), signal(quantity=True), signal(quantity=float("inf")), signal(quantity=0.0205),
                 signal(order_type="limit", price=100.03), signal(direction="flat", side="buy"))
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises((AdapterRefusal, ValueError)): adapter.submit_once(record)
        self.assertEqual(self.venue.submissions, 0)

    def test_stale_and_future_signals_are_rejected_before_reservation(self):
        for timestamp in ("2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"):
            record = signal(); record["decision_time_utc"] = timestamp
            with self.assertRaisesRegex(AdapterRefusal, "stale or has a future"):
                self.adapter().submit_once(record)
        self.assertEqual(self.venue.submissions, 0)

    def test_idempotency_survives_adapter_restart(self):
        record = signal()
        first = self.adapter().submit_once(record)
        with self.assertRaisesRegex(AdapterRefusal, "already reserved"): self.adapter().submit_once(record)
        self.assertEqual(self.venue.submissions, 1)
        self.assertEqual(first["outcome"], "ACKNOWLEDGED")
        journal = json.loads(self.adapter().reservation_store.get("s-1")["intent_json"])
        self.assertEqual(journal["signal"]["signal_id"], "s-1")
        self.assertEqual(journal["venue_evidence"]["mode"], "SIMULATED")
        self.assertIn("submit_simulated_order", journal["tool_schemas"])
        child = "import sys; from idempotency import ReservationStore, DuplicateIntent; s=ReservationStore(sys.argv[1]); row=s.get('s-1');\ntry: s.reserve('s-1', row['request_digest'], '2026-10-18T00:00:00Z', row['intent_json'])\nexcept DuplicateIntent: print('blocked'); raise SystemExit(23)\nraise SystemExit(0)"
        env = dict(__import__("os").environ, PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, "-c", child, str(self.db)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout.strip(), "blocked")

    def test_concurrent_adapters_atomically_reserve_once(self):
        adapters = [self.adapter(), self.adapter()]
        barrier = threading.Barrier(2)
        record = signal()
        def submit(adapter):
            barrier.wait()
            try: adapter.submit_once(record); return "accepted"
            except AdapterRefusal: return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(submit, adapters))
        self.assertCountEqual(results, ["accepted", "blocked"])
        self.assertEqual(self.venue.submissions, 1)

    def test_concurrent_processes_atomically_reserve_once(self):
        import idempotency
        idempotency.ReservationStore(self.db)
        context = multiprocessing.get_context("spawn")
        start = context.Event(); results = context.Queue()
        workers = [context.Process(target=_competing_reservation, args=(str(self.db), start, results)) for _ in range(2)]
        for worker in workers: worker.start()
        start.set()
        for worker in workers: worker.join(15)
        self.assertTrue(all(worker.exitcode == 0 for worker in workers))
        self.assertCountEqual([results.get(timeout=2), results.get(timeout=2)], ["reserved", "blocked"])

    def test_crash_after_reservation_blocks_a_later_dispatch(self):
        import idempotency
        record = signal(); digest = "c" * 64
        store = idempotency.ReservationStore(self.db)
        store.reserve(record["signal_id"], digest, now(), "{}")
        with self.assertRaisesRegex(AdapterRefusal, "already reserved"):
            self.adapter().submit_once(record)
        self.assertEqual(self.venue.submissions, 0)

    def test_uncertain_acknowledgement_remains_blocked_after_restart(self):
        venue = FakeVenue(fail=True); record = signal()
        with self.assertRaisesRegex(AdapterRefusal, "SUBMISSION_UNKNOWN"): self.adapter(venue).submit_once(record)
        with self.assertRaisesRegex(AdapterRefusal, r"already reserved \(UNKNOWN\)"): self.adapter(venue).submit_once(record)
        self.assertEqual(venue.submissions, 1)

    def test_reconciliation_is_bound_to_reserved_intent_and_retained(self):
        adapter = self.adapter(); receipt = adapter.submit_once(signal())
        with self.assertRaises(AdapterRefusal): adapter.reconcile_order("fdx-not-registered")
        result = adapter.reconcile_order(receipt["client_order_id"])
        row = adapter.reservation_store.get_by_client_order_id(receipt["client_order_id"])
        self.assertEqual(json.loads(row["reconciliation_json"]), result)

    def test_journal_export_is_deterministic_anchored_and_non_mutating(self):
        import idempotency
        receipt = self.adapter().submit_once(signal())
        before = idempotency.ReservationStore(self.db).get("s-1")
        out = pathlib.Path(self.temp.name) / "runs"
        self.assertEqual(export_journal(self.db, out), 0)
        artifact_path = out / "s-1.json"
        first = artifact_path.read_bytes()
        artifact = json.loads(first)
        self.assertEqual(artifact["status"], "ACKNOWLEDGED")
        self.assertEqual(artifact["provenance"]["journal_created_at_utc"]["value"], before["created_at_utc"])
        self.assertEqual(artifact["provenance"]["receipt"]["digest_status"], "ANCHORED_IN_JOURNAL")
        self.assertEqual(artifact["provenance"]["receipt"]["digest"], before["receipt_digest"])
        self.assertEqual(export_journal(self.db, out), 0)
        self.assertEqual(artifact_path.read_bytes(), first)
        self.assertEqual(idempotency.ReservationStore(self.db).get("s-1"), before)
        artifact_path.write_text("conflict", encoding="utf-8")
        self.assertEqual(export_journal(self.db, out), 1)

    def test_journal_export_rejects_tamper_and_marks_legacy_receipt(self):
        self.adapter().submit_once(signal())
        db = sqlite3.connect(self.db)
        try: db.execute("UPDATE paper_intents SET receipt_digest=NULL WHERE signal_id='s-1'"); db.commit()
        finally: db.close()
        out = pathlib.Path(self.temp.name) / "runs"
        self.assertEqual(export_journal(self.db, out), 0)
        artifact = json.loads((out / "s-1.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["provenance"]["receipt"]["digest_status"], "COMPUTED_AT_EXPORT_LEGACY")
        db = sqlite3.connect(self.db)
        try: db.execute("UPDATE paper_intents SET request_digest=? WHERE signal_id='s-1'", ("0" * 64,)); db.commit()
        finally: db.close()
        other = pathlib.Path(self.temp.name) / "tamper"
        self.assertEqual(export_journal(self.db, other), 1)

    def test_journal_export_reserved_intents_distinguish_dispatch(self):
        import idempotency
        store = idempotency.ReservationStore(self.db)
        record = signal()
        timestamp = now()
        schema_digest = hashlib.sha256(json.dumps(OPS, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        evidence = {"mode":"SIMULATED", "account_id":"paper-1", "observed_at_utc":timestamp, "retrieved_at_utc":timestamp, "tool_schema_digest":schema_digest}
        metadata = self.venue.get_instrument_metadata("BTC-PERP")
        request = {"account_id":"paper-1", "asset":record["asset"], "order":record["order"], "signal_id":record["signal_id"],
                   "signal_digest":hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "instrument_metadata_digest":hashlib.sha256(json.dumps({k:v for k,v in metadata.items() if k not in {"observed_at_utc","retrieved_at_utc"}}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "tool_schema_digest":schema_digest}
        request_digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        intent = {"signal":record,"venue_evidence":evidence,"instrument_metadata":metadata,"tool_schemas":OPS,"request":request,"request_digest":request_digest}
        store.reserve("s-1",request_digest,timestamp,json.dumps(intent,sort_keys=True,separators=(",", ":")))
        out=pathlib.Path(self.temp.name)/"runs"; self.assertEqual(export_journal(self.db,out),0)
        artifact=json.loads((out/"s-1.json").read_text(encoding="utf-8")); self.assertEqual(artifact["status"],"PREPARED")

    def test_lifecycle_receipt_invariants_and_incomplete_nonfill(self):
        result = evaluation_completeness({"order_id": None, "fills": [], "fees": None, "funding": None, "account_equity": 10000, "equity_history": None})
        self.assertFalse(result["complete"]); self.assertIsNone(result["metrics"])
        base = {"schema_version": "paper-run.v1", "run_id": "r1", "status": "PREPARED", "signal": signal(), "venue_evidence": None, "submission": None,
                "reconciliation": {"order_id": None, "fills": None, "fees": None, "funding": None, "account_equity": None, "equity_history": None}, "evaluation": evaluation_completeness({})}
        self.assertTrue(validate_run(base))
        with self.assertRaises(ValueError): validate_run(dict(base, status="ACKNOWLEDGED"))
        timestamp = now()
        missing = {"complete": False, "missing_fields": ["fees", "funding", "equity_history"], "metrics": None}
        nonfill = dict(base, status="RECONCILED", venue_evidence={"mode": "SIMULATED", "account_id": "paper-1", "observed_at_utc": timestamp, "retrieved_at_utc": timestamp, "tool_schema_digest": hashlib.sha256(json.dumps(OPS, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
                       submission={"outcome": "ACKNOWLEDGED", "client_order_id": "fdx-test", "request_digest": "a" * 64, "order_id": "o-1", "acknowledged": True, "acknowledged_at_utc": timestamp},
                       reconciliation={"order_id": "o-1", "fills": [], "fees": None, "funding": None, "account_equity": 10000, "equity_history": None}, evaluation=missing)
        self.assertTrue(validate_run(nonfill))
        mismatched = dict(nonfill, reconciliation={**nonfill["reconciliation"], "order_id": "different-order"})
        with self.assertRaisesRegex(ValueError, "must match"): validate_run(mismatched)
        unknown = dict(base, status="SUBMISSION_UNKNOWN", venue_evidence=nonfill["venue_evidence"],
                       submission={"outcome": "UNKNOWN", "client_order_id": "fdx-test", "request_digest": "a" * 64, "acknowledged": True})
        with self.assertRaisesRegex(ValueError, "durable intent receipt"): validate_run(unknown)

if __name__ == "__main__": unittest.main()
