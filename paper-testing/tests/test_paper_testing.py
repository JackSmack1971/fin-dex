import hashlib
import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapter import AdapterRefusal, PaperAdapter, evaluation_completeness
from validate_paper_record import validate_run, validate_signal


OPS = {"get_paper_status": "status-v1", "submit_simulated_order": "order-v1", "reconcile_order": "reconcile-v1"}


def signal(gate="QUALIFIED"):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {"schema_version": "signal.v1", "signal_id": "s-1", "decision_time_utc": now,
            "asset": {"venue": "Liquid", "instrument_id": "BTC-PERP", "base": "BTC", "quote": "USD", "instrument_type": "perpetual"},
            "direction": "long", "horizon_days": 14, "rationale": "fixture", "evidence_refs": ["run:abc"], "gate_result": gate,
            "order": {"side": "buy", "order_type": "market", "quantity": 0.01, "time_in_force": "IOC"}}


class FakeVenue:
    def __init__(self, mode="SIMULATED", fail=False): self.mode, self.fail, self.submissions = mode, fail, 0
    def get_paper_status(self):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        digest = hashlib.sha256(json.dumps(OPS, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"mode": self.mode, "account_id": "paper-1", "observed_at_utc": now, "retrieved_at_utc": now, "tool_schema_digest": digest}
    def submit_simulated_order(self, request):
        self.submissions += 1
        if self.fail: raise TimeoutError("uncertain")
        return {"order_id": "o-1", "client_order_id": request["client_order_id"], "acknowledged": True}
    def reconcile_order(self, client_order_id): return {"order_id": None, "fills": None}


class PaperTestingTests(unittest.TestCase):
    def adapter(self, venue=None, ops=None): return PaperAdapter(venue or FakeVenue(), ops or OPS, OPS)

    def test_valid_and_rejected_signals_are_records(self):
        self.assertTrue(validate_signal(signal()))
        self.assertTrue(validate_signal(signal("REJECTED")))
        bad = signal(); bad["unexpected"] = True
        with self.assertRaises(ValueError): validate_signal(bad)

    def test_unknown_or_changed_schema_fails_closed(self):
        with self.assertRaises(AdapterRefusal): self.adapter(ops={**OPS, "place_order": "new"})
        changed = {**OPS, "submit_simulated_order": "changed"}
        with self.assertRaises(AdapterRefusal): self.adapter(ops=changed)

    def test_live_or_stale_or_malformed_status_rejected(self):
        with self.assertRaises(AdapterRefusal): self.adapter(FakeVenue("LIVE")).verify_paper_mode()
        adapter = self.adapter()
        evidence = adapter.verify_paper_mode()
        evidence["retrieved_at_utc"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(AdapterRefusal): adapter.submit_once(signal(), evidence, "cid-1")
        old_signal = signal(); old_signal["decision_time_utc"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(AdapterRefusal): adapter.submit_once(old_signal, adapter.verify_paper_mode(), "cid-old")
        class Missing(FakeVenue):
            def get_paper_status(self): return {"mode": "SIMULATED"}
        with self.assertRaises(AdapterRefusal): self.adapter(Missing()).verify_paper_mode()

    def test_rejected_signal_and_duplicate_are_blocked(self):
        adapter = self.adapter(); evidence = adapter.verify_paper_mode()
        with self.assertRaises(AdapterRefusal): adapter.submit_once(signal("REJECTED"), evidence, "cid-x")
        adapter.submit_once(signal(), evidence, "cid-1")
        with self.assertRaises(AdapterRefusal): adapter.submit_once(signal(), evidence, "cid-1")

    def test_uncertain_submission_is_never_retried(self):
        venue = FakeVenue(fail=True); adapter = self.adapter(venue); evidence = adapter.verify_paper_mode()
        with self.assertRaisesRegex(AdapterRefusal, "SUBMISSION_UNKNOWN"): adapter.submit_once(signal(), evidence, "cid-1")
        with self.assertRaises(AdapterRefusal): adapter.submit_once(signal(), evidence, "cid-1")
        self.assertEqual(venue.submissions, 1)

    def test_nonfill_missing_inputs_keep_metrics_incomplete(self):
        result = evaluation_completeness({"order_id": "o-1", "fills": [], "fees": None, "funding": None, "account_equity": 10000, "equity_history": None})
        self.assertFalse(result["complete"]); self.assertIsNone(result["metrics"])
        run = {"schema_version": "paper-run.v1", "run_id": "r1", "status": "PREPARED", "signal": signal(), "venue_evidence": None, "submission": None,
               "reconciliation": {"order_id": "o-1", "fills": [], "fees": None, "funding": None, "account_equity": 10000, "equity_history": None},
               "evaluation": result}
        self.assertTrue(validate_run(run))
        run["evaluation"] = {"complete": True, "missing_fields": [], "metrics": {}}
        with self.assertRaises(ValueError): validate_run(run)


if __name__ == "__main__": unittest.main()
