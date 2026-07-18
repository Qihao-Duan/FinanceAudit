"""evalx.run -- regression metrics runner (Agent F).

Usage:
    python3 -m evalx.run [--build build]
                         [--findings PATH] [--candidates PATH]
                         [--registry PATH] [--out PATH]
                         [--fixtures] [--strict]

Defaults (real pipeline):
    findings   = <build>/findings.json
    candidates = <build>/candidates.json
    registry   = <build>/audit.duckdb   (tables source_registry + doc_units)
    out        = <build>/eval_report.json

--fixtures switches every default to evalx/fixtures/*.fixture.json and the
output to <build>/eval_report.fixture.json; the CODE PATHS are identical --
only input locations change.

Exit codes:
    0  all mandatory gates pass
    1  citation resolvability < 100% (contract-mandated hard gate), or
       registry unavailable so resolvability cannot be proven
    1  (with --strict) additionally on any target miss, decoy FP, or gating
       amount-assertion failure
    2  input files missing / unreadable

No LLM is used anywhere in this module (llm_used: false).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from evalx.labels import (
    TARGETS, DECOYS, FRAUD_IMPLYING_SCHEMES, FRAUD_WORDING_TERMS,
)

# ------------------------------------------------------------------ loading

def _load_json(path: Path, what: str):
    if not path.exists():
        print(f"[evalx] FATAL: {what} not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[evalx] FATAL: cannot parse {what} {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _as_list(obj, key):
    """Accept both a bare list and {key: [...]} wrappers."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get(key), list):
        return obj[key]
    print(f"[evalx] FATAL: expected a list (or {{'{key}': [...]}}), got {type(obj)}",
          file=sys.stderr)
    sys.exit(2)


class Registry:
    """source_registry + doc_units, backed by duckdb or a JSON fixture."""

    def __init__(self):
        self.available = False
        self.backend = None
        self.source_files = {}      # source_id -> file
        self.doc_unit_ids = set()   # source_ids present in doc_units
        self.doc_unit_text = {}     # source_id -> text (fixture / small tables)
        self.has_doc_units = False

    @classmethod
    def load(cls, path: Path):
        reg = cls()
        if not path.exists():
            return reg
        if path.suffix == ".json":
            data = _load_json(path, "registry fixture")
            for row in data.get("source_registry", []):
                reg.source_files[str(row["source_id"])] = row.get("file", "")
            for row in data.get("doc_units", []):
                sid = str(row["source_id"])
                reg.doc_unit_ids.add(sid)
                reg.doc_unit_text[sid] = row.get("text", "")
            reg.has_doc_units = "doc_units" in data
            reg.available = True
            reg.backend = f"json:{path}"
            return reg
        # duckdb backend (real pipeline)
        try:
            import duckdb
            con = duckdb.connect(str(path), read_only=True)
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            if "source_registry" in tables:
                for sid, file in con.execute(
                        "SELECT source_id, file FROM source_registry").fetchall():
                    reg.source_files[str(sid)] = file or ""
                reg.available = True
            if "doc_units" in tables:
                reg.has_doc_units = True
                for sid, text in con.execute(
                        "SELECT source_id, text FROM doc_units").fetchall():
                    sid = str(sid)
                    reg.doc_unit_ids.add(sid)
                    reg.doc_unit_text[sid] = text or ""
            con.close()
            reg.backend = f"duckdb:{path}"
        except Exception as exc:  # noqa: BLE001
            print(f"[evalx] WARNING: cannot read registry {path}: {exc}",
                  file=sys.stderr)
        return reg


# ------------------------------------------------------------ text matching

def _numeric_token_found(token: str, text: str) -> bool:
    return re.search(rf"(?<![0-9]){re.escape(token)}(?![0-9])", text) is not None


def _tokens_match(numeric, textual, blob: str):
    hits = [t for t in numeric if _numeric_token_found(t, blob)]
    hits += [t for t in textual if t in blob]
    return hits


def _serialize(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str).lower()


_NARRATIVE_KEYS = {
    "mechanism", "assertion", "description", "narrative", "summary", "note",
    "text", "quote", "expected_evidence", "missing_evidence", "pbc_requests",
    "counterevidence", "entity_label", "name", "title", "rule_name",
}


def _narrative_text(obj, key=None, acc=None) -> str:
    """Concatenate narrative-ish string fields, EXCLUDING the scheme enum."""
    if acc is None:
        acc = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "scheme":
                continue
            _narrative_text(v, k, acc)
    elif isinstance(obj, list):
        for v in obj:
            _narrative_text(v, key, acc)
    elif isinstance(obj, str) and key in _NARRATIVE_KEYS:
        acc.append(obj)
    return " \n ".join(acc).lower()


def _fraud_worded(finding) -> list:
    text = _narrative_text(finding)
    return [t for t in FRAUD_WORDING_TERMS if t in text]


# ------------------------------------------------------------------ metrics

def eval_targets(findings, candidates):
    results = []
    for t in TARGETS:
        fam = set(t["scheme_family"])
        hit_findings, near_misses = [], []
        for f in findings:
            blob = _serialize(f)
            toks = _tokens_match(t["entity_tokens_numeric"],
                                 t["entity_tokens_text"], blob)
            if not toks:
                continue
            if f.get("disposition") == "report" and f.get("scheme") in fam:
                hit_findings.append(
                    {"finding_id": f.get("finding_id"),
                     "scheme": f.get("scheme"), "matched_tokens": toks})
            else:
                near_misses.append(
                    {"finding_id": f.get("finding_id"),
                     "scheme": f.get("scheme"),
                     "disposition": f.get("disposition"),
                     "matched_tokens": toks})
        covering = []
        for c in candidates:
            blob = _serialize(c)
            toks = _tokens_match(t["entity_tokens_numeric"],
                                 t["entity_tokens_text"], blob)
            if toks:
                covering.append({"candidate_id": c.get("candidate_id"),
                                 "rule_id": c.get("rule_id"),
                                 "matched_tokens": toks})
        results.append({
            "target_id": t["target_id"],
            "name": t["name"],
            "finding_hit": bool(hit_findings),
            "hit_findings": hit_findings,
            "near_misses": near_misses,          # matched entity, wrong scheme/disposition
            "candidate_covered": bool(covering),
            "covering_candidates": covering,
            "amount_check": _amount_check(t, [h["finding_id"] for h in hit_findings],
                                          findings),
        })
    return results


def _iter_amount_claims(finding):
    for c in finding.get("claims", []):
        v = c.get("value") or {}
        if v.get("kind") == "amount" and isinstance(v.get("value"), (int, float)):
            yield c, Decimal(str(v["value"]))


def _amount_check(target, hit_ids, findings):
    spec = target.get("amount_assertion")
    if not spec:
        return {"status": "not_specified"}
    if not hit_ids:
        return {"status": "target_missed", "gating": spec.get("gating", False)}
    hit_set = set(hit_ids)
    hits = [f for f in findings if f.get("finding_id") in hit_set]
    if spec["kind"] == "exact":
        want = Decimal(str(spec["value"]))
        for f in hits:
            for claim, val in _iter_amount_claims(f):
                if val == want:
                    return {"status": "pass", "kind": "exact",
                            "expected": float(want), "found": float(val),
                            "claim_id": claim.get("claim_id"),
                            "gating": spec.get("gating", False)}
        return {"status": "fail", "kind": "exact", "expected": float(want),
                "found": None,
                "detail": "no amount claim equals the expected value exactly",
                "gating": spec.get("gating", False)}
    # range assertion (F3)
    lo, hi = Decimal(str(spec["min"])), Decimal(str(spec["max"]))
    phrases = [p.lower() for p in spec.get("required_phrases", [])]
    best = None
    for f in hits:
        for claim, val in _iter_amount_claims(f):
            if lo <= val <= hi:
                assertion = (claim.get("assertion") or "").lower()
                phrase_ok = any(p in assertion for p in phrases) if phrases else True
                cand = {"status": "pass" if phrase_ok else "partial",
                        "kind": "range", "range": [float(lo), float(hi)],
                        "found": float(val), "claim_id": claim.get("claim_id"),
                        "phrase_present": phrase_ok,
                        "required_phrases": phrases,
                        "gating": spec.get("gating", False)}
                if phrase_ok:
                    return cand
                best = best or cand
    if best:
        best["detail"] = ("amount in range but none of the required label "
                          "phrases appears in the claim assertion")
        return best
    return {"status": "fail", "kind": "range", "range": [float(lo), float(hi)],
            "found": None,
            "detail": "no amount claim inside the expected range",
            "gating": spec.get("gating", False)}


def eval_decoys(findings):
    results, fp_count = [], 0
    for d in DECOYS:
        mentioned, fp_hits, observed_ok = [], [], []
        for f in findings:
            blob = _serialize(f)
            toks = _tokens_match(d["tokens_numeric"], d["tokens_text"], blob)
            if not toks:
                continue
            terms = _fraud_worded(f)
            scheme_fraud = f.get("scheme") in FRAUD_IMPLYING_SCHEMES
            entry = {"finding_id": f.get("finding_id"),
                     "disposition": f.get("disposition"),
                     "scheme": f.get("scheme"),
                     "matched_tokens": toks,
                     "fraud_terms": terms,
                     "scheme_implies_fraud": scheme_fraud}
            mentioned.append(entry)
            if f.get("disposition") == "report" and (terms or scheme_fraud):
                fp_hits.append(entry)
            elif f.get("disposition") in ("observation", "report"):
                observed_ok.append(entry)
        res = {"decoy_id": d["decoy_id"], "name": d["name"],
               "expected_grade": d["grade"],
               "mentioned_in": mentioned,
               "false_positive": bool(fp_hits),
               "false_positive_findings": fp_hits}
        if d["grade"] == "observation_expected":
            res["conflict_surfaced_neutrally"] = bool(observed_ok)
            res["note"] = ("neutral observation-level surfacing is CORRECT "
                           "and expected for this item")
        fp_count += bool(fp_hits)
        results.append(res)
    return results, fp_count


def eval_citations(findings, registry: Registry):
    total, resolved = 0, 0
    unresolved, warnings = [], []
    for f in findings:
        fid = f.get("finding_id")
        for c in f.get("claims", []):
            for cit in c.get("citations", []):
                total += 1
                sid = str(cit.get("source_id", "") or "")
                loc = {"finding_id": fid, "claim_id": c.get("claim_id"),
                       "source_id": sid, "kind": cit.get("kind"),
                       "file": cit.get("file")}
                if not registry.available:
                    unresolved.append({**loc, "reason": "registry unavailable"})
                    continue
                if not sid or sid not in registry.source_files:
                    unresolved.append({**loc, "reason": "source_id not in source_registry"})
                    continue
                reg_file = registry.source_files[sid]
                if cit.get("file") and reg_file and cit["file"] != reg_file:
                    unresolved.append({**loc,
                                       "reason": f"file mismatch (registry says {reg_file})"})
                    continue
                if cit.get("kind") == "passage" and registry.has_doc_units \
                        and sid not in registry.doc_unit_ids:
                    unresolved.append({**loc, "reason": "passage source_id not in doc_units"})
                    continue
                # soft quality check: quote should re-appear in the doc unit text
                if cit.get("kind") == "passage" and cit.get("quote"):
                    text = registry.doc_unit_text.get(sid, "")
                    if text:
                        norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
                        if norm(cit["quote"]) not in norm(text):
                            warnings.append({**loc, "warning": "quote not found in doc unit text "
                                                               "(locator degradation, not a failure)"})
                resolved += 1
    pct = 100.0 if total == 0 else round(100.0 * resolved / total, 2)
    return {"total": total, "resolved": resolved, "resolvability_pct": pct,
            "unresolved": unresolved, "quality_warnings": warnings,
            "registry_backend": registry.backend,
            "registry_available": registry.available}


def eval_candidate_sources(candidates, registry: Registry):
    """Informational: do candidate source_ids resolve too?"""
    total, resolved, unresolved = 0, 0, []
    for c in candidates:
        for sid in c.get("source_ids", []):
            total += 1
            if registry.available and str(sid) in registry.source_files:
                resolved += 1
            else:
                unresolved.append({"candidate_id": c.get("candidate_id"),
                                   "source_id": sid})
    pct = 100.0 if total == 0 else round(100.0 * resolved / total, 2)
    return {"total": total, "resolved": resolved, "resolvability_pct": pct,
            "unresolved": unresolved[:50]}


_FINDING_DENOM_KEYS = ("population_size", "rule_hits",
                       "peers_with_expected_evidence", "defender_rejected_hits")
_CANDIDATE_DENOM_KEYS = ("population_size", "rule_hits",
                         "peers_with_expected_evidence")


def eval_gates(findings, candidates):
    disp, rep = {}, {}
    missing_denoms_f, missing_denoms_c = [], []
    for f in findings:
        disp[f.get("disposition", "MISSING")] = disp.get(f.get("disposition", "MISSING"), 0) + 1
        rep[f.get("reporting_status", "MISSING")] = rep.get(f.get("reporting_status", "MISSING"), 0) + 1
        d = f.get("denominators")
        if not isinstance(d, dict) or any(k not in d for k in _FINDING_DENOM_KEYS):
            missing_denoms_f.append(f.get("finding_id"))
    for c in candidates:
        d = c.get("denominators")
        if not isinstance(d, dict) or any(k not in d for k in _CANDIDATE_DENOM_KEYS):
            missing_denoms_c.append(c.get("candidate_id"))
    return {"findings_total": len(findings),
            "candidates_total": len(candidates),
            "dispositions": disp,
            "reporting_status": rep,
            "findings_missing_denominators": missing_denoms_f,
            "candidates_missing_denominators": missing_denoms_c,
            "denominators_present_findings":
                len(findings) - len(missing_denoms_f),
            "denominators_present_candidates":
                len(candidates) - len(missing_denoms_c)}


# ------------------------------------------------------------------ summary

def print_summary(report):
    line = "-" * 78
    print()
    print(line)
    print("EVALX SUMMARY".center(78))
    print(line)
    print(f"{'target':<6} {'name':<40} {'cand?':<6} {'find?':<6} {'amount':<12}")
    for t in report["targets"]:
        amt = t["amount_check"]["status"]
        print(f"{t['target_id']:<6} {t['name'][:40]:<40} "
              f"{'YES' if t['candidate_covered'] else 'no':<6} "
              f"{'HIT' if t['finding_hit'] else 'MISS':<6} {amt:<12}")
    fr = report["scores"]
    print(line)
    print(f"finding-level recall  : {fr['finding_recall']}   "
          f"candidate-level recall: {fr['candidate_recall']}")
    print(f"{'decoy':<6} {'expected':<24} {'FP?':<5} note")
    for d in report["decoys"]:
        extra = ""
        if d["expected_grade"] == "observation_expected":
            extra = ("conflict surfaced neutrally"
                     if d.get("conflict_surfaced_neutrally")
                     else "conflict NOT surfaced")
        elif d["mentioned_in"]:
            extra = f"mentioned in {len(d['mentioned_in'])} finding(s), neutral"
        print(f"{d['decoy_id']:<6} {d['expected_grade']:<24} "
              f"{'FP!' if d['false_positive'] else 'ok':<5} {extra}")
    print(line)
    cit = report["citations"]
    print(f"citations: {cit['resolved']}/{cit['total']} resolvable "
          f"({cit['resolvability_pct']}%)  "
          f"quality warnings: {len(cit['quality_warnings'])}")
    ccit = report["candidate_source_ids"]
    print(f"candidate source_ids: {ccit['resolved']}/{ccit['total']} "
          f"({ccit['resolvability_pct']}%) [informational]")
    g = report["gate_stats"]
    print(f"dispositions: {g['dispositions']}  "
          f"denominators present: findings "
          f"{g['denominators_present_findings']}/{g['findings_total']}, "
          f"candidates {g['denominators_present_candidates']}/{g['candidates_total']}")
    print(line)
    print(f"EXIT CODE: {report['exit_code']}"
          + ("  (citation gate failed)" if report["exit_code"] else "  (all gates pass)"))
    print(line)


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog="evalx.run")
    ap.add_argument("--build", default="build")
    ap.add_argument("--findings")
    ap.add_argument("--candidates")
    ap.add_argument("--registry")
    ap.add_argument("--out")
    ap.add_argument("--fixtures", action="store_true",
                    help="use evalx/fixtures/* instead of build artifacts")
    ap.add_argument("--strict", action="store_true",
                    help="also exit nonzero on target misses / decoy FPs / "
                         "gating amount failures")
    args = ap.parse_args(argv)

    build = Path(args.build)
    fixdir = Path(__file__).resolve().parent / "fixtures"
    findings_path = Path(args.findings) if args.findings else (
        fixdir / "findings.fixture.json" if args.fixtures else build / "findings.json")
    candidates_path = Path(args.candidates) if args.candidates else (
        fixdir / "candidates.fixture.json" if args.fixtures else build / "candidates.json")
    registry_path = Path(args.registry) if args.registry else (
        fixdir / "source_registry.fixture.json" if args.fixtures else build / "audit.duckdb")
    out_path = Path(args.out) if args.out else (
        build / ("eval_report.fixture.json" if args.fixtures else "eval_report.json"))

    findings = _as_list(_load_json(findings_path, "findings"), "findings")
    candidates = _as_list(_load_json(candidates_path, "candidates"), "candidates")
    registry = Registry.load(registry_path)
    if not registry.available:
        print(f"[evalx] WARNING: registry not available at {registry_path}; "
              f"citation resolvability CANNOT be proven -> gate fails",
              file=sys.stderr)

    targets = eval_targets(findings, candidates)
    decoys, fp_count = eval_decoys(findings)
    citations = eval_citations(findings, registry)
    cand_cites = eval_candidate_sources(candidates, registry)
    gates = eval_gates(findings, candidates)

    n_hit = sum(t["finding_hit"] for t in targets)
    n_cov = sum(t["candidate_covered"] for t in targets)
    amount_gate_failures = [
        t["target_id"] for t in targets
        if t["amount_check"].get("gating")
        and t["amount_check"]["status"] in ("fail", "target_missed")]

    citation_gate_ok = citations["registry_available"] \
        and citations["resolvability_pct"] == 100.0
    exit_code = 0 if citation_gate_ok else 1
    if args.strict and (n_hit < len(targets) or fp_count > 0
                        or amount_gate_failures):
        exit_code = max(exit_code, 1)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm_used": False,
        "inputs": {"findings": str(findings_path),
                   "candidates": str(candidates_path),
                   "registry": str(registry_path),
                   "fixture_mode": bool(args.fixtures),
                   "strict": bool(args.strict)},
        "scores": {
            "finding_recall": f"{n_hit}/{len(targets)}",
            "candidate_recall": f"{n_cov}/{len(targets)}",
            "decoy_false_positives": fp_count,
            "citation_resolvability_pct": citations["resolvability_pct"],
            "amount_gate_failures": amount_gate_failures,
        },
        "targets": targets,
        "decoys": decoys,
        "citations": citations,
        "candidate_source_ids": cand_cites,
        "gate_stats": gates,
        "exit_code": exit_code,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"[evalx] wrote {out_path}")

    print_summary(report)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
