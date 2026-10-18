import json
import pathlib
import tempfile
import unittest
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from proposal_workflow import ProposalError, ProposalStore
from export_proposals import export_proposals


def details():
    return {"account_id":"paper-account", "instrument":{"venue":"Example","instrument_id":"BTC-USD","base":"BTC","quote":"USD","instrument_type":"spot"},
            "side":"buy", "quantity":"0.25", "quantity_unit":"BTC", "order_type":"limit", "research_provenance":["research-runs/run/observations.json"]}


class ProposalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "proposals.sqlite3"
        self.store = ProposalStore(self.db)

    def tearDown(self): self.tmp.cleanup()

    def test_create_validate_export_is_separate_and_immutable(self):
        row = self.store.create("proposal-1", details())
        self.assertEqual((row["state"], row["external_attempt_status"]), ("DRAFT", "NOT_ATTEMPTED"))
        validated = self.store.validate("proposal-1")
        self.assertEqual(validated["state"], "VALIDATED")
        with self.assertRaisesRegex(ProposalError, "duplicate proposal_id"):
            self.store.create("proposal-1", {**details(), "quantity":"0.5"})
        out = pathlib.Path(self.tmp.name) / "exports"
        self.assertEqual(export_proposals(self.db, out), 0)
        record = json.loads((out / "proposal-1.json").read_text())
        self.assertEqual(record["details_digest"], validated["details_digest"])
        self.assertEqual(record["external_attempt_status"], "NOT_ATTEMPTED")
        self.assertFalse((pathlib.Path(self.tmp.name) / "paper-idempotency.sqlite3").exists())

    def test_missing_account_and_instrument_metadata_fail_validation(self):
        for missing in ("account_id", "instrument"):
            item = details(); item.pop(missing)
            self.store.create("p-" + missing, item)
            with self.assertRaises(ProposalError): self.store.validate("p-" + missing)

    def test_authorization_is_bound_and_external_call_stays_disabled(self):
        row = self.store.create("p-auth", details()); row = self.store.validate("p-auth")
        with self.assertRaisesRegex(ProposalError, "does not match"):
            self.store.authorize("p-auth", "0" * 64, "suggest_order", "user", "approved")
        authorized = self.store.authorize("p-auth", row["details_digest"], "suggest_order", "user", "explicit approval")
        self.assertEqual(authorized["external_attempt_status"], "AUTHORIZED_PENDING")
        with self.assertRaisesRegex(ProposalError, "EXTERNAL_OPERATION_DISABLED"):
            self.store.reserve_dispatch("p-auth", row["details_digest"], "suggest_order", "user")
        after = self.store.get("p-auth")
        self.assertEqual((after["state"], after["external_attempt_status"]), ("VALIDATED", "AUTHORIZED_PENDING"))

    def test_changed_details_require_new_identity(self):
        row = self.store.create("p-old", details())
        self.assertNotEqual(row["details_digest"], self.store.create("p-new", {**details(), "quantity":"0.5"})["details_digest"])


if __name__ == "__main__": unittest.main()
