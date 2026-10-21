#!/usr/bin/env python3
"""Offline, fail-closed validator for typed-review classification records.

Implements the *currently controlling* "Typed review classification",
"Research review gate" and "Engineering review requirements" sections of the
repository AGENTS.md. It does not change, broaden or reinterpret that policy.

WHAT THIS CHECKS: that a *recorded* classification is structurally complete
and internally consistent with the policy (declared fields, declared class,
declared prerequisites, and, when several records are supplied together,
declared splits and lineage). It never classifies from prose.

WHAT THIS DOES NOT DO: it does not judge review quality or truth, does not
authenticate reviewers or maintainers, does not re-run or replace
`native_lifecycle.py prepare-review` / `validate-review`, and is not a runtime
security boundary. Codex does not route reviewer dispatch through it, so a
session can still dispatch without consulting it. A passing result is a
supervisor-checkpoint aid, never an authorization token.

Exit codes: 0 every record valid and dispatch-eligible by its own record;
1 at least one record or cross-record check is invalid (fail closed);
2 usage or unreadable input; 3 all records valid but at least one is not
dispatchable (UNRESOLVED, POLICY_DECISION, BLOCKED gate, ineligible reviewer,
or NO_DISPATCH declared). Only exit 0 may be read as "record permits dispatch".
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

SCHEMA_VERSION = 1
RESEARCH, ENGINEERING, UNRESOLVED, POLICY = (
    "RESEARCH_REVIEW", "ENGINEERING_REVIEW", "UNRESOLVED", "POLICY_DECISION")
CLASSES = (RESEARCH, ENGINEERING, UNRESOLVED, POLICY)
PERMITTED, NO_DISPATCH = "DISPATCH_PERMITTED", "NO_DISPATCH"
DISPOSITIONS = (PERMITTED, NO_DISPATCH)

RESEARCH_ASPECTS = ("RESEARCH_EVIDENCE", "RESEARCH_METHODOLOGY", "RESEARCH_CONCLUSIONS")
ENGINEERING_ASPECTS = ("IMPLEMENTATION_PROPERTIES",)
POLICY_ASPECTS = ("REVIEW_AUTHORITY_CHANGE",)
UNRESOLVED_ASPECTS = ("UNDETERMINED",)
ASPECTS_BY_CLASS = {RESEARCH: RESEARCH_ASPECTS, ENGINEERING: ENGINEERING_ASPECTS,
                    POLICY: POLICY_ASPECTS, UNRESOLVED: UNRESOLVED_ASPECTS}

TOP_REQUIRED = ("schema_version", "record_id", "recorded_at_utc", "request", "classification",
                "target", "requested_decision", "decision_aspects", "scope",
                "evidence_references", "prerequisites", "dispatch_disposition", "class_fields")
TOP_OPTIONAL = ("exception_ref", "supersedes_record_id")
POLICY_CHANGE_KINDS = ("CHANGE_DISPATCH_AUTHORITY", "ALTER_OR_REMOVE_GATE",
                       "REDEFINE_REVIEW_AUTHORITY", "ACCEPT_POLICY_AMENDMENT")
REQUIRED_PREREQS = {
    RESEARCH: ("PREPARE_REVIEW_PERMIT",),
    ENGINEERING: ("GIT_BASELINE", "EXACT_TARGET", "VERIFICATION_EVIDENCE", "REVIEWER_INDEPENDENCE"),
}
REPRESENTATION_FLAGS = ("approves_research_conclusions", "issues_or_substitutes_research_permit",
                        "satisfies_prepare_review", "waives_gate_or_authorizes_policy_change",
                        "authorizes_liquid_execution")
# AGENTS.md "One-time independent engineering review exception": consumed by one
# engagement; pr-drafts/10 records that engagement's terminal PASS. Any new
# citation is therefore rejected (a new engagement needs separate authorization).
EXCEPTION_REGISTRY = {
    "agents-policy-scope-repair-2026-10-18": {"status": "CONSUMED", "applies_to": ENGINEERING},
}

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_GIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DISCLAIMER = ("Checks declared structure and policy consistency only; not review quality or truth. "
              "Not a runtime security boundary; does not replace prepare-review/validate-review.")


# --------------------------------------------------------------------------- helpers
def _shape(value, path, required, optional, errs, tag):
    if not isinstance(value, dict):
        errs.append(f"{path}: must be an object [{tag}]")
        return False
    for key in sorted(set(required) - set(value)):
        errs.append(f"{path}.{key}: required [{tag}]")
    for key in sorted(set(value) - set(required) - set(optional)):
        errs.append(f"{path}.{key}: unknown field [{tag}]")
    return True


def _text(obj, key, path, errs, tag):
    if key not in obj:
        return None
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        errs.append(f"{path}.{key}: must be a nonblank string [{tag}]")
        return None
    return value


def _enum(obj, key, path, allowed, errs, tag):
    if key not in obj:
        return None
    value = obj[key]
    if not isinstance(value, str) or value not in allowed:
        errs.append(f"{path}.{key}: must be one of {sorted(allowed)} [{tag}]")
        return None
    return value


def _bool(obj, key, path, errs, tag):
    if key not in obj:
        return None
    if not isinstance(obj[key], bool):
        errs.append(f"{path}.{key}: must be boolean [{tag}]")
        return None
    return obj[key]


def _hex(obj, key, path, pattern, errs, tag):
    if key not in obj:
        return None
    value = obj[key]
    if not isinstance(value, str) or not pattern.fullmatch(value) or set(value) == {"0"}:
        errs.append(f"{path}.{key}: must be a lowercase non-zero hex digest/commit [{tag}]")
        return None
    return value


def _strings(obj, key, path, errs, tag, nonempty=False):
    if key not in obj:
        return None
    value = obj[key]
    if not isinstance(value, list):
        errs.append(f"{path}.{key}: must be an array [{tag}]")
        return None
    if nonempty and not value:
        errs.append(f"{path}.{key}: must not be empty [{tag}]")
    good = True
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errs.append(f"{path}.{key}[{i}]: must be a nonblank string [{tag}]")
            good = False
    return value if good else None


def _items(obj, key, path, required, optional, errs, tag, nonempty=False):
    """Validate an array of objects; return the list of well-shaped dicts."""
    if key not in obj:
        return []
    value = obj[key]
    if not isinstance(value, list):
        errs.append(f"{path}.{key}: must be an array [{tag}]")
        return []
    if nonempty and not value:
        errs.append(f"{path}.{key}: must not be empty [{tag}]")
    out = []
    for i, item in enumerate(value):
        if _shape(item, f"{path}.{key}[{i}]", required, optional, errs, tag):
            out.append(item)
    return out


def _timestamp(obj, key, path, errs, tag):
    value = _text(obj, key, path, errs, tag)
    if value is None:
        return
    try:
        if not value.endswith("Z"):
            raise ValueError
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
    except ValueError:
        errs.append(f"{path}.{key}: must be a UTC ISO timestamp ending Z [{tag}]")


def _norm_id(value):
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.casefold()


def _norm_text(value):
    return " ".join(value.split()).casefold()


def _fingerprint(identifiers, decision):
    payload = {"t": sorted({_norm_id(i) for i in identifiers}), "d": _norm_text(decision)}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- per-record
class Result:
    def __init__(self, source):
        self.source = source
        self.errors = []
        self.blockers = []          # why a structurally valid record is not dispatchable
        self.record_id = None
        self.classification = None
        self.request_id = None
        self.siblings = []
        self.identifiers = []
        self.included = []
        self.fingerprint = None
        self.disposition = None
        self.gate = None            # research: PASSED / BLOCKED
        self.permit_path = None
        self.supersedes = None
        self.separation_ref = None  # engineering: blocked research record it separates from
        self.acceptance_ref = None
        self.evidence_refs = []

    @property
    def eligible(self):
        return not self.errors and self.disposition == PERMITTED and not self.blockers

    def fail(self, message, tag):
        self.errors.append(f"{message} [{tag}]")

    def as_dict(self):
        return {"source": self.source, "record_id": self.record_id,
                "classification": self.classification, "valid": not self.errors,
                "dispatch_eligible": self.eligible, "errors": self.errors,
                "not_dispatchable_reasons": self.blockers if not self.errors else []}


def _research(cf, res, errs):
    tag = "RRG-PERMIT"
    if not _shape(cf, "class_fields", ("prepare_review",), (), errs, tag):
        return
    if "prepare_review" not in cf:
        return
    pr = cf["prepare_review"]
    if not _shape(pr, "class_fields.prepare_review", ("gate_result", "permit", "checkpoint_output_ref"),
                  (), errs, tag):
        return
    gate = _enum(pr, "gate_result", "class_fields.prepare_review", ("PASSED", "BLOCKED"), errs, tag)
    _text(pr, "checkpoint_output_ref", "class_fields.prepare_review", errs, tag)
    permit = pr.get("permit")
    res.gate = gate
    if gate == "BLOCKED":
        if permit is not None:
            errs.append(f"class_fields.prepare_review.permit: must be null when the gate is BLOCKED [{tag}]")
        res.blockers.append("prepare-review gate BLOCKED: no reviewer may be dispatched for this research decision")
    elif gate == "PASSED":
        if _shape(permit, "class_fields.prepare_review.permit",
                  ("path", "permit_payload_sha256", "state"), (), errs, tag):
            res.permit_path = _text(permit, "path", "class_fields.prepare_review.permit", errs, tag)
            _hex(permit, "permit_payload_sha256", "class_fields.prepare_review.permit", HEX64, errs, tag)
            if permit.get("state") != "REVIEW_DISPATCH_ALLOWED":
                errs.append("class_fields.prepare_review.permit.state: must be REVIEW_DISPATCH_ALLOWED "
                            f"[{tag}]")


def _engineering(cf, res, errs):
    p, tag = "class_fields", "ENG-REQ"
    required = ("git_baseline", "scoped_changes", "source_artifacts", "verification",
                "technical_requirements", "target_contributors", "reviewer",
                "research_exclusions", "representations")
    if not _shape(cf, p, required, ("blocked_research_separation",), errs, tag):
        return
    base = cf.get("git_baseline")
    if "git_baseline" in cf and _shape(base, f"{p}.git_baseline", ("commit", "ref"), (), errs, tag):
        _hex(base, "commit", f"{p}.git_baseline", HEX_GIT, errs, tag)
        _text(base, "ref", f"{p}.git_baseline", errs, tag)
    for item in _items(cf, "scoped_changes", p, ("path", "change"), (), errs, tag, nonempty=True):
        _text(item, "path", f"{p}.scoped_changes[]", errs, tag)
        _enum(item, "change", f"{p}.scoped_changes[]", ("ADDED", "MODIFIED", "DELETED", "RENAMED"), errs, tag)
    for item in _items(cf, "source_artifacts", p, ("path",), ("sha256",), errs, tag):
        _text(item, "path", f"{p}.source_artifacts[]", errs, tag)
        if "sha256" in item:
            _hex(item, "sha256", f"{p}.source_artifacts[]", HEX64, errs, tag)
    ver = cf.get("verification")
    if "verification" in cf and _shape(ver, f"{p}.verification", ("checks_run", "checks_not_run"), (), errs, tag):
        run = _items(ver, "checks_run", f"{p}.verification", ("check", "result", "evidence_ref"), (), errs, tag)
        for item in run:
            _text(item, "check", f"{p}.verification.checks_run[]", errs, tag)
            _enum(item, "result", f"{p}.verification.checks_run[]", ("PASS", "FAIL", "ERROR"), errs, tag)
            _text(item, "evidence_ref", f"{p}.verification.checks_run[]", errs, tag)
        skipped = _items(ver, "checks_not_run", f"{p}.verification", ("check", "reason"), (), errs, tag)
        for item in skipped:
            _text(item, "check", f"{p}.verification.checks_not_run[]", errs, tag)
            _text(item, "reason", f"{p}.verification.checks_not_run[]", errs, tag)
        if not run and not skipped and isinstance(ver.get("checks_run"), list) \
                and isinstance(ver.get("checks_not_run"), list):
            errs.append(f"{p}.verification: record checks run and/or checks not run with reasons [{tag}]")
    _strings(cf, "technical_requirements", p, errs, tag, nonempty=True)
    contributors = _strings(cf, "target_contributors", p, errs, tag)
    excl = _strings(cf, "research_exclusions", p, errs, tag, nonempty=True)
    if excl is not None:
        missing = [a for a in RESEARCH_ASPECTS if a not in excl]
        if missing:
            errs.append(f"{p}.research_exclusions: must expressly exclude {missing} [ENG-NOAPPROVE]")
        extra = [a for a in excl if a not in RESEARCH_ASPECTS]
        if extra:
            errs.append(f"{p}.research_exclusions: unknown exclusion {extra} [ENG-NOAPPROVE]")
    rep = cf.get("representations")
    if "representations" in cf and _shape(rep, f"{p}.representations", REPRESENTATION_FLAGS, (), errs, "ENG-NOAPPROVE"):
        for flag in REPRESENTATION_FLAGS:
            if _bool(rep, flag, f"{p}.representations", errs, "ENG-NOAPPROVE") is True:
                errs.append(f"{p}.representations.{flag}: an engineering record may never claim this "
                            "[ENG-NOAPPROVE]")
    _reviewer(cf.get("reviewer"), contributors, res, errs)
    sep = cf.get("blocked_research_separation")
    if "blocked_research_separation" in cf:
        sp = f"{p}.blocked_research_separation"
        flags = ("independently_meaningful", "separable", "excludes_research_evidence_methodology_conclusions",
                 "not_substitute_for_blocked_decision")
        if _shape(sep, sp, ("blocked_research_record_id", "rationale") + flags, (), errs, "RRG-RELABEL"):
            res.separation_ref = _text(sep, "blocked_research_record_id", sp, errs, "RRG-RELABEL")
            _text(sep, "rationale", sp, errs, "RRG-RELABEL")
            for flag in flags:
                if _bool(sep, flag, sp, errs, "RRG-RELABEL") is False:
                    errs.append(f"{sp}.{flag}: must be true for an engineering decision made after a "
                                "failed research gate [RRG-RELABEL]")


def _reviewer(rv, contributors, res, errs):
    p, tag = "class_fields.reviewer", "ENG-INDEP"
    if not _shape(rv, p, ("identity", "role", "implementation_role", "authorship_disclosure",
                          "conflicts", "independence"), (), errs, tag):
        return
    identity = _text(rv, "identity", p, errs, tag)
    _text(rv, "role", p, errs, tag)   # informational; the role never determines class or eligibility
    role = _enum(rv, "implementation_role", p, ("NONE", "AUTHOR", "IMPLEMENTER", "CONTRIBUTOR", "UNKNOWN"),
                 errs, tag)
    disc = rv.get("authorship_disclosure")
    flags = {}
    if _shape(disc, f"{p}.authorship_disclosure",
              ("authored_target", "implemented_target", "materially_contributed", "statement"), (), errs, tag):
        for key in ("authored_target", "implemented_target", "materially_contributed"):
            flags[key] = _bool(disc, key, f"{p}.authorship_disclosure", errs, tag)
        _text(disc, "statement", f"{p}.authorship_disclosure", errs, tag)
    impartiality = False
    for item in _items(rv, "conflicts", p, ("description", "could_reasonably_affect_impartiality"), (), errs, tag):
        _text(item, "description", f"{p}.conflicts[]", errs, tag)
        if _bool(item, "could_reasonably_affect_impartiality", f"{p}.conflicts[]", errs, tag):
            impartiality = True
    indep = rv.get("independence")
    disposition = None
    if _shape(indep, f"{p}.independence", ("disposition", "basis"), (), errs, tag):
        disposition = _enum(indep, "disposition", f"{p}.independence", ("INDEPENDENT", "INELIGIBLE"), errs, tag)
        _text(indep, "basis", f"{p}.independence", errs, tag)
    reasons = [name for name, value in flags.items() if value]
    if role is not None and role != "NONE":
        reasons.append(f"implementation_role={role}")
    if identity and contributors and _norm_text(identity) in {_norm_text(c) for c in contributors}:
        reasons.append("reviewer listed in target_contributors")
    if impartiality:
        reasons.append("conflict could reasonably affect impartiality")
    if disposition == "INDEPENDENT" and reasons:
        errs.append(f"{p}.independence.disposition: INDEPENDENT contradicts disclosures {reasons} [{tag}]")
    if disposition == "INELIGIBLE":
        res.blockers.append("reviewer declared INELIGIBLE: replace the reviewer before dispatch")


def _unresolved(cf, errs):
    if _shape(cf, "class_fields", ("clarification_needed",), (), errs, "TRC-UNRESOLVED"):
        _strings(cf, "clarification_needed", "class_fields", errs, "TRC-UNRESOLVED", nonempty=True)


def _policy(cf, res, errs):
    p, tag = "class_fields", "TRC-POLICY"
    if not _shape(cf, p, ("policy_change_kind", "acceptance"), (), errs, tag):
        return
    _enum(cf, "policy_change_kind", p, POLICY_CHANGE_KINDS, errs, tag)
    acc = cf.get("acceptance")
    if not isinstance(acc, dict):
        errs.append(f"{p}.acceptance: must be an object [{tag}]")
        return
    state = _enum(acc, "state", f"{p}.acceptance", ("PENDING", "ACCEPTED"), errs, tag)
    extra = ("record_ref", "accepted_by", "accepted_at_utc")
    if state == "PENDING":
        _shape(acc, f"{p}.acceptance", ("state",), (), errs, tag)
    elif state == "ACCEPTED":
        if _shape(acc, f"{p}.acceptance", ("state",) + extra, (), errs, tag):
            res.acceptance_ref = _text(acc, "record_ref", f"{p}.acceptance", errs, tag)
            _timestamp(acc, "accepted_at_utc", f"{p}.acceptance", errs, tag)
            by = acc.get("accepted_by")
            if _shape(by, f"{p}.acceptance.accepted_by", ("identity", "role", "authority_basis"), (), errs, tag):
                _text(by, "identity", f"{p}.acceptance.accepted_by", errs, tag)
                _text(by, "authority_basis", f"{p}.acceptance.accepted_by", errs, tag)
                if by.get("role") != "AUTHORIZED_MAINTAINER":
                    errs.append(f"{p}.acceptance.accepted_by.role: must be AUTHORIZED_MAINTAINER [{tag}]")
    elif state is None and "state" not in acc:
        errs.append(f"{p}.acceptance.state: required [{tag}]")


def validate_record(doc, source="<memory>"):
    res = Result(source)
    errs = res.errors
    tag = "TRC-RECORD"
    if not _shape(doc, "$", TOP_REQUIRED, TOP_OPTIONAL, errs, tag):
        return res
    version = doc.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        errs.append(f"$.schema_version: must be {SCHEMA_VERSION} [{tag}]")
    rid = doc.get("record_id")
    if isinstance(rid, str) and ID.fullmatch(rid):
        res.record_id = rid
    elif "record_id" in doc:
        errs.append(f"$.record_id: must match {ID.pattern} [{tag}]")
    _timestamp(doc, "recorded_at_utc", "$", errs, tag)

    req = doc.get("request")
    if _shape(req, "$.request", ("request_id", "sibling_record_ids"), (), errs, tag):
        rq = req.get("request_id")
        if isinstance(rq, str) and ID.fullmatch(rq):
            res.request_id = rq
        else:
            errs.append(f"$.request.request_id: must match {ID.pattern} [{tag}]")
        sibs = _strings(req, "sibling_record_ids", "$.request", errs, "TRC-SPLIT")
        if sibs is not None:
            res.siblings = sibs
            if len(set(sibs)) != len(sibs) or res.record_id in sibs:
                errs.append("$.request.sibling_record_ids: must be unique and exclude this record [TRC-SPLIT]")

    cls = _enum(doc, "classification", "$", CLASSES, errs, "TRC-OBJECT")
    res.classification = cls
    tgt = doc.get("target")
    ids = []
    if _shape(tgt, "$.target", ("kind", "identifiers", "description"), (), errs, "TRC-OBJECT"):
        _text(tgt, "kind", "$.target", errs, "TRC-OBJECT")   # informational; never determines class
        _text(tgt, "description", "$.target", errs, "TRC-OBJECT")
        ids = _strings(tgt, "identifiers", "$.target", errs, "TRC-OBJECT",
                       nonempty=cls in (RESEARCH, ENGINEERING)) or []
        if len({_norm_id(i) for i in ids}) != len(ids):
            errs.append("$.target.identifiers: must be unique [TRC-OBJECT]")
    res.identifiers = [_norm_id(i) for i in ids]
    decision = _text(doc, "requested_decision", "$", errs, "TRC-OBJECT")
    if decision is not None and ids:
        res.fingerprint = _fingerprint(ids, decision)

    _aspects(doc, cls, errs)

    scope = doc.get("scope")
    if _shape(scope, "$.scope", ("included", "excluded"), (), errs, "TRC-OBJECT"):
        inc = _strings(scope, "included", "$.scope", errs, "TRC-OBJECT",
                       nonempty=cls in (RESEARCH, ENGINEERING))
        _strings(scope, "excluded", "$.scope", errs, "ENG-NOAPPROVE", nonempty=cls == ENGINEERING)
        res.included = sorted({_norm_text(i) for i in inc or []})
    for item in _items(doc, "evidence_references", "$", ("kind", "ref"), ("sha256",), errs, "TRC-RECORD",
                       nonempty=cls in (RESEARCH, ENGINEERING)):
        _text(item, "kind", "$.evidence_references[]", errs, "TRC-RECORD")
        ref = _text(item, "ref", "$.evidence_references[]", errs, "TRC-RECORD")
        if ref:
            res.evidence_refs.append(_norm_id(ref))
        if "sha256" in item:
            _hex(item, "sha256", "$.evidence_references[]", HEX64, errs, "TRC-RECORD")

    prereqs = {}
    for item in _items(doc, "prerequisites", "$", ("id", "description", "status"), (), errs, "TRC-RECORD"):
        pid = _text(item, "id", "$.prerequisites[]", errs, "TRC-RECORD")
        _text(item, "description", "$.prerequisites[]", errs, "TRC-RECORD")
        status = _enum(item, "status", "$.prerequisites[]", ("MET", "UNMET"), errs, "TRC-RECORD")
        if pid in prereqs:
            errs.append(f"$.prerequisites: duplicate id {pid} [TRC-RECORD]")
        if pid and status:
            prereqs[pid] = status
    res.disposition = _enum(doc, "dispatch_disposition", "$", DISPOSITIONS, errs, "TRC-RECORD")

    cf = doc.get("class_fields")
    handlers = {RESEARCH: lambda: _research(cf, res, errs), ENGINEERING: lambda: _engineering(cf, res, errs),
                UNRESOLVED: lambda: _unresolved(cf, errs), POLICY: lambda: _policy(cf, res, errs)}
    if cls in handlers:
        handlers[cls]()
    if cls == UNRESOLVED:
        res.blockers.append("UNRESOLVED blocks dispatch until clarified, recorded and reclassified")
    elif cls == POLICY:
        res.blockers.append("POLICY_DECISION is never a reviewer-dispatch type; acceptance is a separate "
                            "maintainer decision")

    _prerequisites(cls, prereqs, res, errs)
    if res.disposition == PERMITTED:
        if cls in (UNRESOLVED, POLICY):
            errs.append(f"$.dispatch_disposition: {cls} is never dispatchable [TRC-{'UNRESOLVED' if cls == UNRESOLVED else 'POLICY'}]")
        for pid, status in prereqs.items():
            if status != "MET":
                errs.append(f"$.dispatch_disposition: DISPATCH_PERMITTED with unmet prerequisite {pid} [TRC-RECORD]")
        if res.blockers and cls in (RESEARCH, ENGINEERING):
            errs.append(f"$.dispatch_disposition: DISPATCH_PERMITTED contradicts: {'; '.join(res.blockers)} "
                        "[TRC-RECORD]")
    elif res.disposition == NO_DISPATCH:
        res.blockers.append("record declares NO_DISPATCH")

    if "exception_ref" in doc:
        _exception(doc["exception_ref"], cls, errs)
    if "supersedes_record_id" in doc:
        sid = doc["supersedes_record_id"]
        if isinstance(sid, str) and ID.fullmatch(sid) and sid != res.record_id:
            res.supersedes = sid
        else:
            errs.append("$.supersedes_record_id: must be another record's id [TRC-RECORD]")
    return res


def _aspects(doc, cls, errs):
    if "decision_aspects" not in doc:
        return
    aspects = doc["decision_aspects"]
    allowed = set(RESEARCH_ASPECTS + ENGINEERING_ASPECTS + POLICY_ASPECTS + UNRESOLVED_ASPECTS)
    if not isinstance(aspects, list) or not aspects or any(not isinstance(a, str) or a not in allowed
                                                           for a in aspects):
        errs.append(f"$.decision_aspects: must be a nonempty array drawn from {sorted(allowed)} [TRC-OBJECT]")
        return
    if len(set(aspects)) != len(aspects):
        errs.append("$.decision_aspects: must be unique [TRC-OBJECT]")
    kinds = {c for c, group in ASPECTS_BY_CLASS.items() if set(aspects) & set(group)}
    if len(kinds) > 1:
        errs.append("$.decision_aspects: mixes research, engineering and/or policy decisions; split into "
                    "separately recorded targets, scopes and outcomes [TRC-SPLIT]")
    elif cls in ASPECTS_BY_CLASS and not set(aspects) <= set(ASPECTS_BY_CLASS[cls]):
        errs.append(f"$.decision_aspects: aspects {sorted(set(aspects))} do not match classification {cls}; "
                    "the class follows the decision requested, not file type, location, role or origin "
                    "[TRC-OBJECT]")


def _prerequisites(cls, prereqs, res, errs):
    for pid in REQUIRED_PREREQS.get(cls, ()):
        if pid not in prereqs:
            errs.append(f"$.prerequisites: {pid} must be recorded for {cls} [TRC-RECORD]")
    if cls == RESEARCH and res.gate and "PREPARE_REVIEW_PERMIT" in prereqs:
        if (prereqs["PREPARE_REVIEW_PERMIT"] == "MET") != (res.gate == "PASSED"):
            errs.append("$.prerequisites.PREPARE_REVIEW_PERMIT: status contradicts the recorded gate_result "
                        "[RRG-PERMIT]")


def _exception(ref, cls, errs):
    tag = "EXC-ONETIME"
    if not _shape(ref, "$.exception_ref", ("id",), (), errs, tag):
        return
    ident = _text(ref, "id", "$.exception_ref", errs, tag)
    if ident is None:
        return
    entry = EXCEPTION_REGISTRY.get(ident)
    if entry is None:
        errs.append(f"$.exception_ref.id: unrecognized exception {ident!r}; none is authorized [{tag}]")
        return
    if entry["status"] == "CONSUMED":
        errs.append(f"$.exception_ref.id: exception {ident!r} is consumed and expired; a new engagement needs "
                    f"separate explicit authorization [{tag}]")
    if cls != entry["applies_to"]:
        errs.append(f"$.exception_ref: exception applies only to {entry['applies_to']}; it is not a "
                    f"research-review exception or a reusable gate bypass [{tag}]")


# --------------------------------------------------------------------------- cross-record
def validate_set(results):
    """Cross-record checks over every record supplied together."""
    by_id = {}
    for r in results:
        if r.record_id:
            if r.record_id in by_id:
                r.fail(f"duplicate record_id {r.record_id}", "TRC-RECORD")
                by_id[r.record_id].fail(f"duplicate record_id {r.record_id}", "TRC-RECORD")
            else:
                by_id[r.record_id] = r
    names = {pathlib.PurePath(r.source).name.casefold() for r in results}

    for r in results:
        if not r.request_id:
            continue
        group = [x for x in results if x is not r and x.request_id == r.request_id]
        declared, present = set(r.siblings), {x.record_id for x in group if x.record_id}
        for missing in sorted(declared - present):
            r.fail(f"sibling {missing} is not supplied with the same request_id; supply every related record",
                   "TRC-SPLIT")
        for extra in sorted(present - declared):
            r.fail(f"record {extra} shares request_id but is not declared as a split sibling", "TRC-SPLIT")
        for x in group:
            if x.fingerprint and x.fingerprint == r.fingerprint:
                r.fail(f"split sibling {x.record_id} has the same target and decision", "TRC-SPLIT")
            elif x.included and x.included == r.included:
                r.fail(f"split sibling {x.record_id} has the same scope; scopes must be separate", "TRC-SPLIT")

    blocked = [r for r in results if r.classification == RESEARCH and r.gate == "BLOCKED" and r.record_id]
    for r in results:
        if r.supersedes:
            old = by_id.get(r.supersedes)
            if old is None:
                r.fail(f"superseded record {r.supersedes} is not supplied", "TRC-RECORD")
            elif old.classification == UNRESOLVED:
                pass
            elif old.classification == RESEARCH and old.gate == "BLOCKED":
                if not (r.classification == RESEARCH and r.gate == "PASSED"):
                    r.fail(f"a blocked research decision ({old.record_id}) can be superseded only by a "
                           "RESEARCH_REVIEW whose prepare-review gate passed; relabeling is rejected",
                           "RRG-RELABEL")
            else:
                r.fail("only an UNRESOLVED or gate-BLOCKED research record can be superseded", "TRC-RECORD")
        for b in blocked:
            if r is b:
                continue
            overlap = bool(set(r.identifiers) & set(b.identifiers))
            if r.classification == ENGINEERING:
                if r.fingerprint and r.fingerprint == b.fingerprint:
                    r.fail(f"same target and decision as blocked research record {b.record_id}: relabeling a "
                           "blocked research decision is rejected", "RRG-RELABEL")
                elif (overlap or r.supersedes == b.record_id) and r.separation_ref != b.record_id:
                    r.fail(f"target overlaps blocked research record {b.record_id}; an engineering-only "
                           "decision needs a recorded blocked_research_separation", "RRG-RELABEL")
            elif r.classification == RESEARCH and overlap and r.supersedes != b.record_id:
                r.fail(f"research decision overlaps blocked record {b.record_id}; carving or re-dispatching "
                       "it is rejected unless it supersedes that record with a passed gate", "RRG-RELABEL")
        if r.classification == ENGINEERING and r.separation_ref:
            ref = by_id.get(r.separation_ref)
            if ref is None or not (ref.classification == RESEARCH and ref.gate == "BLOCKED"):
                r.fail("blocked_research_separation must reference a supplied gate-BLOCKED research record",
                       "RRG-RELABEL")
        if r.classification == RESEARCH and r.permit_path:
            if pathlib.PurePath(r.permit_path.replace("\\", "/")).name.casefold() in names:
                r.fail("permit reference points at a supplied classification record; an engineering or "
                       "classification record cannot satisfy prepare-review", "RRG-PERMIT")
        if r.classification == POLICY and r.acceptance_ref:
            ref = _norm_id(r.acceptance_ref)
            if r.acceptance_ref == r.record_id or r.acceptance_ref in by_id or ref in r.evidence_refs \
                    or pathlib.PurePath(ref).name in names:
                r.fail("acceptance must be a separate maintainer record, not this classification, another "
                       "supplied record, or a cited review finding", "TRC-POLICY")


# --------------------------------------------------------------------------- CLI
def load_json(path):
    raw = pathlib.Path(path).read_bytes()
    if not raw.strip():
        raise ValueError("empty file")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def constant(name):
        raise ValueError(f"non-finite number {name}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except UnicodeError as exc:
        raise ValueError(f"not UTF-8: {exc}") from exc


def run(paths):
    results, unreadable = [], False
    for path in paths:
        try:
            doc = load_json(path)
        except OSError as exc:
            res = Result(str(path))
            res.errors.append(f"unreadable: {exc} [TRC-RECORD]")
            unreadable = True
        except ValueError as exc:
            res = Result(str(path))
            res.errors.append(f"malformed JSON: {exc} [TRC-RECORD]")
        else:
            res = validate_record(doc, str(path))
        results.append(res)
    validate_set(results)
    if unreadable:
        code = 2
    elif any(r.errors for r in results):
        code = 1
    elif any(not r.eligible for r in results):
        code = 3
    else:
        code = 0
    return results, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("records", nargs="+", help="classification record JSON files; pass every related "
                        "record (split siblings, superseded or blocked records) in one invocation")
    args = parser.parse_args(argv)
    results, code = run(args.records)
    print(json.dumps({"exit_code": code, "disclaimer": DISCLAIMER,
                      "records": [r.as_dict() for r in results]}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
