"""Tests for scripts/validate_review_classification.py.

These tests exercise declared-structure and policy-consistency checks only.
They do not evaluate review quality, authenticate anyone, or touch dispatch.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "review-classification"
SCRIPT = ROOT / "scripts" / "validate_review_classification.py"
SCHEMA = ROOT / "assets" / "review-classification-record.v1.schema.json"

spec = importlib.util.spec_from_file_location("review_classification_validator", SCRIPT)
vrc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vrc)

# Rule tag -> exact AGENTS.md text the rule is traced to (guards the traceability table).
CLAUSES = {
    "TRC-RECORD": "record the exact target, requested decision, scope, evidence references, and prerequisites",
    "TRC-OBJECT": "File extension, repository location, reviewer role, and artifact origin do not determine the class.",
    "TRC-SPLIT": "split it into separately recorded targets, scopes, evidence, findings, and outcomes",
    "TRC-UNRESOLVED": "no reviewer dispatch until clarified and recorded",
    "TRC-POLICY": "a review finding or classification cannot accept it",
    "RRG-PERMIT": "before producing a new dispatch permit",
    "RRG-RELABEL": "A failed research gate cannot be bypassed by changing the label, file format, reviewer role, or dividing the same research decision across artifacts or subtargets.",
    "ENG-REQ": "establish and record a known Git baseline, exact review target",
    "ENG-INDEP": "A reviewer who participated in implementing the target is ineligible for it.",
    "ENG-NOAPPROVE": "Engineering findings must never validate, approve, imply, or be represented as approval of research conclusions.",
    "EXC-ONETIME": "It expires when that engagement reaches a terminal outcome.",
}
SCENARIO_NAMES = {
    1: "engineering_complete_without_research_gate", 2: "research_with_permit",
    3: "mixed_request_split", 4: "relabel_after_failed_gate", 5: "research_generated_code_implementation_only",
    6: "source_review_also_certifies_conclusions", 7: "no_git_baseline", 8: "no_exact_target_or_diff_scope",
    9: "reviewer_participated_in_implementation", 10: "ambiguous_request", 11: "policy_amendment_decision",
    12: "reviewer_gate_code_plus_authority_change",
}


def base(name):
    return json.loads((FIXTURES / f"base-{name}.json").read_text(encoding="utf-8"))


def _walk(doc, parts):
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node, parts[-1]


def set_path(doc, dotted, value):
    node, last = _walk(doc, dotted.split("."))
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def delete_path(doc, dotted):
    node, last = _walk(doc, dotted.split("."))
    if isinstance(node, list):
        del node[int(last)]
    else:
        del node[last]


def build(spec_):
    doc = base(spec_["base"])
    for key, value in spec_.get("set", {}).items():
        set_path(doc, key, copy.deepcopy(value))
    for key in spec_.get("delete", []):
        delete_path(doc, key)
    return doc


def exit_of(result):
    return 1 if result.errors else (0 if result.eligible else 3)


class Workspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, doc, name=None):
        path = self.dir / (name or f"{doc.get('record_id', 'record')}.json")
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def run_docs(self, docs):
        results, code = vrc.run([str(self.write(d)) for d in docs])
        return results, code

    def one(self, doc):
        results, _ = self.run_docs([doc])
        return results[0]


def manifest():
    return json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))["cases"]


class MatrixScenarios(Workspace):
    """One named test per pr-drafts/09 matrix scenario; cases live in fixtures/scenarios.json."""

    def run_scenario(self, number):
        cases = [c for c in manifest() if c["scenario"] == number]
        self.assertTrue(cases, f"no fixture cases for scenario {number}")
        for case in cases:
            with self.subTest(case=case["id"]):
                results, code = self.run_docs([build(r) for r in case["records"]])
                self.assertEqual([exit_of(r) for r in results], case["expect"],
                                 [r.errors or r.blockers for r in results])
                for index, needle in case["errors"]:
                    self.assertTrue(any(needle in e for e in results[index].errors),
                                    (needle, results[index].errors))
                self.assertEqual(code, 1 if any(e == 1 for e in case["expect"])
                                 else (3 if any(e == 3 for e in case["expect"]) else 0))
                for result in results:
                    self.assertEqual(result.eligible, exit_of(result) == 0)

    def test_scenario_01_engineering_complete_without_research_gate(self): self.run_scenario(1)
    def test_scenario_02_research_with_permit(self): self.run_scenario(2)
    def test_scenario_03_mixed_request_split(self): self.run_scenario(3)
    def test_scenario_04_relabel_after_failed_gate(self): self.run_scenario(4)
    def test_scenario_05_research_generated_code_implementation_only(self): self.run_scenario(5)
    def test_scenario_06_source_review_also_certifies_conclusions(self): self.run_scenario(6)
    def test_scenario_07_no_git_baseline(self): self.run_scenario(7)
    def test_scenario_08_no_exact_target_or_diff_scope(self): self.run_scenario(8)
    def test_scenario_09_reviewer_participated_in_implementation(self): self.run_scenario(9)
    def test_scenario_10_ambiguous_request(self): self.run_scenario(10)
    def test_scenario_11_policy_amendment_decision(self): self.run_scenario(11)
    def test_scenario_12_reviewer_gate_code_plus_authority_change(self): self.run_scenario(12)

    def test_manifest_covers_exactly_the_twelve_matrix_scenarios(self):
        cases = manifest()
        self.assertEqual({c["scenario"] for c in cases}, set(range(1, 13)))
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for case in cases:
            self.assertIn(case["clause"], CLAUSES)
            self.assertTrue(case["id"].startswith(f"s{case['scenario']:02d}-"))
        self.assertEqual(set(SCENARIO_NAMES), set(range(1, 13)))
        for number in range(1, 13):
            with self.subTest(scenario=number):
                self.assertTrue(any(c["errors"] for c in cases if c["scenario"] == number)
                                or number in (1, 2), "scenario lacks a negative case")

    def test_matrix_document_still_lists_twelve_scenarios(self):
        text = (REPO / "pr-drafts" / "09-review-policy-validation-matrix.md").read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith("| ") and line.split("|")[1].strip().isdigit()]
        self.assertEqual({int(r.split("|")[1]) for r in rows}, set(range(1, 13)))

    def test_clause_traceability_quotes_exist_in_agents_md(self):
        text = " ".join((REPO / "AGENTS.md").read_text(encoding="utf-8").split())
        for tag, quote in CLAUSES.items():
            with self.subTest(tag=tag):
                self.assertIn(" ".join(quote.split()), text)


class HostileCases(Workspace):
    def test_relabeling_survives_case_spacing_and_path_separator_changes(self):
        blocked = build({"base": "research", "set": {
            "record_id": "rec-blocked", "class_fields.prepare_review.gate_result": "BLOCKED",
            "class_fields.prepare_review.permit": None, "prerequisites.0.status": "UNMET",
            "dispatch_disposition": "NO_DISPATCH"}})
        relabel = build({"base": "engineering", "set": {
            "record_id": "rec-relabel", "request.request_id": "req-9",
            "target.identifiers": ["./Research-Runs\\Example\\Synthesis-Snapshot.JSON"],
            "requested_decision": "  DECIDE whether the cited evidence\tsupports the synthesized market claims "}})
        results, code = self.run_docs([blocked, relabel])
        self.assertEqual(code, 1)
        self.assertTrue(any("relabeling a blocked research decision" in e for e in results[1].errors))

    def test_class_is_independent_of_file_extension_location_kind_and_reviewer_role(self):
        locations = ["a.py", "a.json", "a.md", "a.toml", "a.txt", ".agents/research-runs/a.json",
                     "pr-drafts/a.md", ".codex/agents/a.toml", "tests/a.py", "no-extension"]
        for identifier in locations:
            for kind in ("SOURCE_CODE", "JSON", "PROSE", "GENERATED_ARTIFACT", "RESEARCH_PRODUCED_SOURCE"):
                with self.subTest(identifier=identifier, kind=kind):
                    eng = build({"base": "engineering", "set": {
                        "target.identifiers": [identifier], "target.kind": kind,
                        "class_fields.reviewer.role": "research reviewer evidence_verifier"}})
                    res = build({"base": "research", "set": {"target.identifiers": [identifier], "target.kind": kind}})
                    self.assertEqual(exit_of(self.one(eng)), 0)
                    self.assertEqual(exit_of(self.one(res)), 0)

    def test_extension_matching_label_cannot_override_declared_decision(self):
        code_target_research_decision = build({"base": "engineering", "set": {
            "target.identifiers": ["scripts/example_control.py"], "decision_aspects": ["RESEARCH_CONCLUSIONS"]}})
        json_target_authority_change = build({"base": "engineering", "set": {
            "target.identifiers": ["config.json"], "decision_aspects": ["REVIEW_AUTHORITY_CHANGE"]}})
        research_labelled_code_review = build({"base": "research", "set": {
            "target.identifiers": ["scripts/example_control.py"], "decision_aspects": ["IMPLEMENTATION_PROPERTIES"]}})
        for doc in (code_target_research_decision, json_target_authority_change, research_labelled_code_review):
            self.assertEqual(exit_of(self.one(doc)), 1)

    def test_one_time_exception_cannot_be_reused_or_invented(self):
        known = next(iter(vrc.EXCEPTION_REGISTRY))
        eng = build({"base": "engineering", "set": {"exception_ref": {"id": known}}})
        self.assertTrue(any("consumed and expired" in e for e in self.one(eng).errors))
        again = build({"base": "engineering", "set": {"record_id": "rec-again", "request.request_id": "req-8",
                                                      "exception_ref": {"id": known}}})
        results, code = self.run_docs([eng, again])
        self.assertEqual(code, 1)
        self.assertTrue(all(exit_of(r) == 1 for r in results))
        unknown = build({"base": "engineering", "set": {"exception_ref": {"id": "made-up"}}})
        self.assertTrue(any("unrecognized exception" in e for e in self.one(unknown).errors))
        research = build({"base": "research", "set": {"exception_ref": {"id": known}}})
        self.assertTrue(any("not a research-review exception" in e for e in self.one(research).errors))
        policy = build({"base": "policy", "set": {"exception_ref": {"id": known}}})
        self.assertEqual(exit_of(self.one(policy)), 1)
        extra = build({"base": "engineering", "set": {"exception_ref": {"id": known, "waives": "prepare-review"}}})
        self.assertEqual(exit_of(self.one(extra)), 1)

    def test_exception_registry_matches_the_retained_evidence(self):
        agents = " ".join((REPO / "AGENTS.md").read_text(encoding="utf-8").split())
        self.assertIn(CLAUSES["EXC-ONETIME"], agents)
        record = (REPO / "pr-drafts" / "10-review-policy-scope-adoption-review.md").read_text(encoding="utf-8")
        self.assertIn("The one-time exception is consumed and expired", record)
        self.assertTrue(all(v["status"] == "CONSUMED" for v in vrc.EXCEPTION_REGISTRY.values()))

    def test_engineering_record_cannot_stand_in_for_a_research_permit(self):
        eng = build({"base": "engineering"})
        results, _ = self.run_docs([eng])
        self.assertEqual(exit_of(results[0]), 0)
        no_permit = build({"base": "research", "set": {"class_fields.prepare_review.permit": None}})
        self.assertEqual(exit_of(self.one(no_permit)), 1)
        # An engineering record offered as the research record's "permit" is rejected.
        res = build({"base": "research", "set": {"class_fields.prepare_review.permit.path": "x/rec-engineering.json"}})
        results, _ = self.run_docs([res, eng])
        self.assertEqual(exit_of(results[0]), 1)

    def test_strict_json_loading_fails_closed(self):
        cases = {
            "empty.json": b"",
            "notjson.json": b"{",
            "dupe.json": b'{"schema_version": 1, "schema_version": 1}',
            "nan.json": b'{"schema_version": NaN}',
            "array.json": b"[]",
            "binary.json": b"\xff\xfe\x00",
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                path = self.dir / name
                path.write_bytes(payload)
                results, code = vrc.run([str(path)])
                self.assertEqual(code, 1)
                self.assertTrue(results[0].errors)
        results, code = vrc.run([str(self.dir / "missing.json")])
        self.assertEqual(code, 2)

    def test_top_level_type_and_version_traps(self):
        for key, value in (("schema_version", True), ("schema_version", 2), ("schema_version", 1.0),
                           ("unexpected", 1), ("recorded_at_utc", "2026-10-21T00:00:00+00:00"),
                           ("recorded_at_utc", "2026-13-01T00:00:00Z"), ("record_id", "bad id")):
            with self.subTest(key=key, value=value):
                doc = base("engineering")
                doc[key] = value
                self.assertEqual(exit_of(self.one(doc)), 1)

    def test_duplicate_record_ids_are_rejected(self):
        a, b = base("engineering"), base("engineering")
        paths = [self.write(a, "one.json"), self.write(b, "two.json")]
        results, code = vrc.run([str(p) for p in paths])
        self.assertEqual(code, 1)
        self.assertTrue(all(any("duplicate record_id" in e for e in r.errors) for r in results))


class RequiredFieldMutation(Workspace):
    """Mutating any single required field of a valid record must not leave it dispatch-eligible."""

    @staticmethod
    def mutations(node, prefix=()):
        if isinstance(node, dict):
            for key, value in node.items():
                path = prefix + (key,)
                yield path, value
                yield from RequiredFieldMutation.mutations(value, path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, (dict, list)):
                    yield from RequiredFieldMutation.mutations(value, prefix + (index,))

    @staticmethod
    def apply(doc, path, op):
        doc = copy.deepcopy(doc)
        node = doc
        for part in path[:-1]:
            node = node[part]
        if op == "delete":
            del node[path[-1]]
        elif op == "null":
            node[path[-1]] = None
        elif op == "blank":
            node[path[-1]] = ""
        elif op == "flip":
            node[path[-1]] = not node[path[-1]]
        return doc

    def sweep(self, name, eligible):
        valid = base(name)
        self.assertEqual(exit_of(self.one(valid)), 0 if eligible else 3)
        checked = 0
        for path, value in self.mutations(valid):
            ops = ["delete", "null"]
            if isinstance(value, str):
                ops.append("blank")
            if isinstance(value, bool):
                ops.append("flip")
            for op in ops:
                with self.subTest(record=name, path=".".join(map(str, path)), op=op):
                    result = self.one(self.apply(valid, path, op))
                    self.assertEqual(exit_of(result), 1, result.errors or result.blockers)
                    checked += 1
        self.assertGreater(checked, 40)

    def test_research_record(self): self.sweep("research", True)
    def test_engineering_record(self): self.sweep("engineering", True)
    def test_unresolved_record(self): self.sweep("unresolved", False)
    def test_policy_record(self): self.sweep("policy", False)

    def test_unknown_field_at_every_object_level_is_rejected(self):
        for name in ("research", "engineering", "unresolved", "policy"):
            valid = base(name)
            objects = [()] + [p for p, v in self.mutations(valid) if isinstance(v, dict)]
            for path in objects:
                with self.subTest(record=name, path=path):
                    doc = copy.deepcopy(valid)
                    node = doc
                    for part in path:
                        node = node[part]
                    node["surprise"] = True
                    self.assertEqual(exit_of(self.one(doc)), 1)


class CommandLine(Workspace):
    def run_cli(self, *paths):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, paths)],
                              capture_output=True, text=True, timeout=60)

    def test_exit_codes_and_report(self):
        eligible = self.write(base("engineering"))
        done = self.run_cli(eligible)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertTrue(report["records"][0]["dispatch_eligible"])
        self.assertIn("not review quality or truth", report["disclaimer"])
        self.assertIn("Not a runtime security boundary", report["disclaimer"])
        self.assertEqual(self.run_cli(self.write(base("policy"))).returncode, 3)
        bad = base("engineering")
        del bad["class_fields"]["git_baseline"]
        self.assertEqual(self.run_cli(self.write(bad, "bad.json")).returncode, 1)
        self.assertEqual(self.run_cli(self.dir / "absent.json").returncode, 2)
        self.assertNotEqual(self.run_cli().returncode, 0)

    def test_validator_does_not_modify_inputs(self):
        path = self.write(base("research"))
        before = path.read_bytes()
        self.run_cli(path)
        self.assertEqual(before, path.read_bytes())

    def test_validator_stays_decoupled_from_the_research_gate(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("import native_lifecycle", source)
        self.assertNotIn("subprocess", source)


class SchemaAndDocs(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_schema_is_versioned_and_matches_validator_constants(self):
        s = self.schema
        self.assertEqual(s["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("review-classification-record-v1", s["$id"])
        self.assertEqual(s["properties"]["schema_version"], {"const": vrc.SCHEMA_VERSION})
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(set(s["required"]), set(vrc.TOP_REQUIRED))
        self.assertEqual(set(s["properties"]), set(vrc.TOP_REQUIRED) | set(vrc.TOP_OPTIONAL))
        self.assertEqual(tuple(s["properties"]["classification"]["enum"]), vrc.CLASSES)
        self.assertEqual(tuple(s["properties"]["dispatch_disposition"]["enum"]), vrc.DISPOSITIONS)
        aspects = vrc.RESEARCH_ASPECTS + vrc.ENGINEERING_ASPECTS + vrc.POLICY_ASPECTS + vrc.UNRESOLVED_ASPECTS
        self.assertEqual(set(s["properties"]["decision_aspects"]["items"]["enum"]), set(aspects))
        defs = s["$defs"]
        self.assertEqual(tuple(defs["policy_fields"]["properties"]["policy_change_kind"]["enum"]), vrc.POLICY_CHANGE_KINDS)
        eng = defs["engineering_fields"]
        self.assertEqual(set(eng["properties"]["representations"]["required"]), set(vrc.REPRESENTATION_FLAGS))
        self.assertEqual(set(eng["required"]) | {"blocked_research_separation"}, set(eng["properties"]))
        for cls, key in ((vrc.RESEARCH, "research_fields"), (vrc.ENGINEERING, "engineering_fields"),
                         (vrc.UNRESOLVED, "unresolved_fields"), (vrc.POLICY, "policy_fields")):
            name = {vrc.RESEARCH: "research", vrc.ENGINEERING: "engineering",
                    vrc.UNRESOLVED: "unresolved", vrc.POLICY: "policy"}[cls]
            self.assertEqual(set(defs[key]["required"]), set(base(name)["class_fields"]))

    def test_schema_if_then_covers_each_class_once(self):
        consts = [b["if"]["properties"]["classification"]["const"] for b in self.schema["allOf"]]
        self.assertEqual(sorted(consts), sorted(vrc.CLASSES))

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema not installed")
    def test_schema_agrees_with_validator_on_structural_cases(self):
        import jsonschema
        validator = jsonschema.Draft202012Validator(self.schema)
        for name in ("research", "engineering", "unresolved", "policy"):
            self.assertEqual(list(validator.iter_errors(base(name))), [], name)
        negatives = [
            ("engineering", lambda d: d["class_fields"].pop("git_baseline")),
            ("engineering", lambda d: d["class_fields"]["representations"].update(approves_research_conclusions=True)),
            ("engineering", lambda d: d["decision_aspects"].append("RESEARCH_CONCLUSIONS")),
            ("research", lambda d: d["class_fields"]["prepare_review"]["permit"].update(state="BLOCKED")),
            ("unresolved", lambda d: d.update(dispatch_disposition="DISPATCH_PERMITTED")),
            ("policy", lambda d: d.update(dispatch_disposition="DISPATCH_PERMITTED")),
            ("policy", lambda d: d["class_fields"]["acceptance"].update(record_ref="x")),
        ]
        for name, mutate in negatives:
            doc = base(name)
            mutate(doc)
            self.assertTrue(list(validator.iter_errors(doc)), (name, doc))

    def test_documentation_points_at_the_validator_with_limits_stated(self):
        handoffs = (ROOT / "references" / "agent-handoffs.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references" / "review-classification.md").read_text(encoding="utf-8")
        for text in (handoffs, skill):
            self.assertIn("validate_review_classification.py", text)
            self.assertIn("review-classification.md", text)
        flat = " ".join(reference.split())
        self.assertIn("not review quality or truth", flat)
        self.assertIn("not a runtime security boundary", flat.lower())
        self.assertIn("review-classification-record.v1.schema.json", flat)
        for tag in CLAUSES:
            self.assertIn(tag, reference, tag)


if __name__ == "__main__":
    unittest.main()
