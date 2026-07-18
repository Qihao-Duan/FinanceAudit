#!/usr/bin/env python3
"""Robustness / schema-adaptivity test harness (Agent: robustness).

The pipeline claims to be *schema-adaptive*: index.xml-driven GDPdU parsing, column
profiling with semantic aliasing, threshold three-level fallback, population-scope /
observability gates and "quarantine instead of crash". Judging happens on a NEW dossier
whose structure may differ from data/practice. This harness stress-tests those claims
BEFORE the finals do.

Each scenario S1..S7:
  * copies data/practice -> data/robust_<name>/ and applies ONE targeted perturbation,
  * runs the FULL pipeline (ingest -> finder -> evidence -> claims -> defense -> verdict
    -> eval) into build_rob_<name>/,
  * asserts the expected robustness behaviour,
  * records finding-/candidate-count drift vs the untouched baseline (build_rob_baseline).

Prints a PASS/FAIL table + a drift table and writes build_rob_report.json.
Exit 0 iff every scenario's must-pass checks hold.

Usage:
    python3 -m evalx.robustness_test [--only S1,S3] [--keep] [--rebuild-baseline]

See evalx/ROBUSTNESS.md for the findings, the minimal fixes made, and documented limits.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRACTICE = REPO / "data" / "practice"
BEGLEIT = "Begleitdokumente"
PY = sys.executable


# --------------------------------------------------------------------------- io

def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _as_list(obj, key):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get(key), list):
        return obj[key]
    return []


def load_artifacts(build: Path) -> dict:
    manifest = _read_json(build / "manifest.json") or {}
    cands = _as_list(_read_json(build / "candidates.json") or [], "candidates")
    finds = _as_list(_read_json(build / "findings.json") or [], "findings")
    return {
        "build": build,
        "manifest": manifest,
        "manifest_files": {f["file"]: f for f in manifest.get("files", [])},
        "semantic_conflicts": manifest.get("semantic_conflicts", []),
        "candidates": cands,
        "findings": finds,
        "finder_meta": _read_json(build / "finder_meta.json") or {},
        "thresholds": _read_json(build / "thresholds.json") or {},
        "eval": _read_json(build / "eval_report.json") or {},
    }


def cands_by_rule(art, rule_id):
    return [c for c in art["candidates"] if c.get("rule_id") == rule_id]


def per_rule_counts(art):
    return (art.get("finder_meta") or {}).get("per_rule_counts", {})


def dispositions(art):
    d = {}
    for f in art["findings"]:
        k = f.get("disposition", "MISSING")
        d[k] = d.get(k, 0) + 1
    return d


def finding_text_blob(f) -> str:
    """Lower-cased narrative text of a finding (assertions, descriptions, mechanism, ...)."""
    parts = []

    def walk(o, key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and key in {
            "assertion", "description", "mechanism", "narrative", "summary",
            "note", "text", "entity_label", "missing_evidence", "expected_evidence",
        }:
            parts.append(o)
    walk(f)
    return " \n ".join(parts).lower()


# --------------------------------------------------------------------- pipeline

def run_pipeline(data_dir: Path, build_dir: Path) -> tuple[int, str]:
    if build_dir.exists():
        shutil.rmtree(build_dir)
    cmd = [PY, "scripts/run_pipeline.py",
           "--data", str(data_dir.relative_to(REPO)),
           "--build", str(build_dir.relative_to(REPO))]
    env = {"FA_DATA_DIR": str(data_dir), "FA_BUILD_DIR": str(build_dir)}
    import os
    full_env = dict(os.environ, **env)
    p = subprocess.run(cmd, cwd=str(REPO), env=full_env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def fresh_copy(name: str) -> Path:
    dst = REPO / "data" / f"robust_{name}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(PRACTICE, dst)
    # drop macOS cruft so it never confuses the unrecognized-file scan
    for junk in dst.rglob(".DS_Store"):
        junk.unlink()
    return dst


# ------------------------------------------------------------- perturbations

def perturb_s1(dst: Path):
    (dst / BEGLEIT / "Pruefungsplanung_JET_2025.docx").unlink()


def perturb_s2(dst: Path):
    (dst / BEGLEIT / "Wareneingangsliste_2025.csv").unlink()


def perturb_s3(dst: Path):
    """Swap the DECLARED order/labels of two VariableColumn blocks (SACHKONTONUMMER <->
    GEGENKONTO) in Sachkonten/index.xml WITHOUT touching the txt data — simulates wrong
    column declarations. The positional GDPdU mappers must stay correct; the profiler must
    record the resulting declared-vs-observed conflict."""
    idx = dst / "Sachkonten" / "index.xml"
    tree = ET.parse(idx)
    root = tree.getroot()
    swapped = False
    for t in root.iter("Table"):
        url = (t.findtext("URL") or "")
        if "buchungen" not in url.lower():
            continue
        names = {}
        for vc in t.iter("VariableColumn"):
            n = vc.find("Name")
            if n is not None and n.text in ("SACHKONTONUMMER", "GEGENKONTO"):
                names[n.text] = n
        if {"SACHKONTONUMMER", "GEGENKONTO"} <= set(names):
            names["SACHKONTONUMMER"].text, names["GEGENKONTO"].text = (
                names["GEGENKONTO"].text, names["SACHKONTONUMMER"].text)
            swapped = True
    if not swapped:
        raise RuntimeError("S3: could not find the two VariableColumn blocks to swap")
    tree.write(idx, encoding="utf-8", xml_declaration=True)


def perturb_s4(dst: Path):
    src = dst / BEGLEIT / "Stammdatenaenderungen_2025.csv"
    src.rename(dst / BEGLEIT / "Masterdata_Changes_2025.csv")


def perturb_s5(dst: Path):
    """Add a plausible but UNKNOWN csv (an untyped payments export)."""
    p = dst / BEGLEIT / "Zahlungsexport_Bank_2025.csv"
    rows = [
        "ZAHLUNG_NR;ZAHLUNGSDATUM;KREDITOR;BETRAG_EUR;VERWENDUNGSZWECK",
        "ZE-0001;05.03.2025;200007;9.690,00;Teilzahlung SAMMEL-200007",
        "ZE-0002;05.03.2025;200007;9.780,00;Teilzahlung SAMMEL-200007",
        "ZE-0003;12.06.2025;200015;1.240,50;Rechnung ER-2025-0421",
    ]
    p.write_text("\n".join(rows) + "\n", encoding="cp1252")


def perturb_s6(dst: Path):
    (dst / "Sachkonten" / "Sachkontobuchungen.txt").write_text("", encoding="cp1252")


def perturb_s8(dst: Path):
    """Delete a NON-core GDPdU sub-ledger (the asset transactions). Must degrade, not fail."""
    (dst / "AV" / "Anlagenbuchungen.txt").unlink()


def perturb_s9(dst: Path):
    (dst / BEGLEIT / "OP-Liste_Kreditoren_2025.xlsx").unlink()


# S7 needs to remember the true first-row credit limit to spot-check the parse.
_S7_STATE: dict = {}


def perturb_s7(dst: Path):
    """Rewrite Kreditlimitliste as UTF-8 with DOT decimals (amount columns), keeping the
    ';' delimiter + headers. Exercises the decimal/encoding adapter path."""
    src = dst / BEGLEIT / "Kreditlimitliste_Debitoren_2025.csv"
    raw = src.read_text(encoding="cp1252")
    lines = raw.splitlines()
    header = lines[0].split(";")
    amount_cols = [i for i, h in enumerate(header)
                   if h.strip() in ("KREDITLIMIT_EUR", "AUSNUTZUNG_31_12_2025_EUR")]
    lim_col = header.index("KREDITLIMIT_EUR")
    acc_col = header.index("DEBITOR")

    def de_to_dot(tok: str) -> str:
        tok = tok.strip()
        if not tok:
            return tok
        return tok.replace(".", "").replace(",", ".")  # 50.000,00 -> 50000.00

    out = [lines[0]]
    first_captured = False
    for ln in lines[1:]:
        if not ln.strip():
            out.append(ln)
            continue
        f = ln.split(";")
        if not first_captured and len(f) > lim_col:
            _S7_STATE["account"] = f[acc_col].strip()
            _S7_STATE["true_limit"] = float(de_to_dot(f[lim_col])) if f[lim_col].strip() else None
            first_captured = True
        for i in amount_cols:
            if i < len(f):
                f[i] = de_to_dot(f[i])
        out.append(";".join(f))
    src.write_text("\n".join(out) + "\n", encoding="utf-8")


# --------------------------------------------------------------------- checks
# Each check fn returns list[(name, ok: bool, detail: str)]. All must be ok to PASS.

def _mfiles(art):
    return art["manifest_files"]


def check_s1(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    th = (art["thresholds"].get("thresholds") or {})
    docx_keys = ["approval_limit_eur", "materiality_overall_eur",
                 "materiality_performance_eur", "jet_sampling_eur"]
    prov = {k: th.get(k, {}).get("provenance") for k in docx_keys}
    r.append(("docx-sourced thresholds fall back to config_default",
              all(prov[k] == "config_default" for k in docx_keys), str(prov)))
    r.append(("provenance is explicitly marked (no silent None)",
              all(prov[k] is not None for k in docx_keys), str(prov)))
    lock = th.get("lock_date", {}).get("provenance")
    r.append(("lock_date still extracted from surviving PDF", lock == "extracted",
              f"lock_date provenance={lock}"))
    # split rule R3 still runs and is unchanged (config default == extracted 10k)
    b3 = cands_by_rule(base, "R03")
    s3 = cands_by_rule(art, "R03")
    same = (len(b3) == len(s3) == 1
            and s3[0]["metrics"].get("sum") == b3[0]["metrics"].get("sum")
            and s3[0]["metrics"].get("limit") == 10000.0
            and s3[0]["entry_ids"] == b3[0]["entry_ids"])
    r.append(("split rule R3 still runs (config-default 10k) & unchanged", same,
              f"baseline={[c['metrics'].get('sum') for c in b3]} scenario={[c['metrics'].get('sum') for c in s3]}"))
    mf = _mfiles(art).get(f"{BEGLEIT}/Pruefungsplanung_JET_2025.docx", {})
    r.append(("missing audit plan recorded as coverage failure in manifest",
              mf.get("parse_coverage") == "failed", f"coverage={mf.get('parse_coverage')}"))
    return r


def check_s2(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    r15 = cands_by_rule(art, "R15")
    obl = [c["metrics"].get("obligation_id") for c in r15]
    r.append(("goods-receipt obligation (OBL-MAT-GR) fires against NO ONE",
              "OBL-MAT-GR" not in obl, f"R15 obligations={obl}"))
    r04 = cands_by_rule(art, "R04")
    degraded = bool(r04) and all(c["metrics"].get("n_goods_receipts_open") == 0
                                 and c["metrics"].get("n_invoice_gr_matches") == 0 for c in r04)
    r.append(("R4 still runs but degrades (0 goods-receipt matches)", degraded,
              f"R04={[(c['metrics'].get('n_goods_receipts_open'), c['metrics'].get('n_invoice_gr_matches')) for c in r04]}"))
    mf = _mfiles(art).get(f"{BEGLEIT}/Wareneingangsliste_2025.csv", {})
    r.append(("missing side table recorded as coverage failure",
              mf.get("parse_coverage") == "failed", f"coverage={mf.get('parse_coverage')}"))
    # absence-language gate: no reported finding claims goods receipts are missing
    phantom = [f.get("finding_id") for f in art["findings"]
               if f.get("disposition") == "report"
               and ("no goods receipt" in finding_text_blob(f)
                    or "no matching goods receipt" in finding_text_blob(f)
                    or "kein wareneingang" in finding_text_blob(f))]
    r.append(("no reported finding asserts a phantom goods-receipt absence",
              not phantom, f"offending={phantom}"))
    return r


def check_s3(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    b3 = cands_by_rule(base, "R03")
    s3 = cands_by_rule(art, "R03")
    identical = (len(b3) == len(s3) == 1
                 and s3[0]["entity_key"] == b3[0]["entity_key"]
                 and s3[0]["metrics"].get("sum") == b3[0]["metrics"].get("sum")
                 and s3[0]["metrics"].get("amounts") == b3[0]["metrics"].get("amounts")
                 and s3[0]["entry_ids"] == b3[0]["entry_ids"])
    r.append(("R3 output IDENTICAL to baseline (positional parse robust; no wrong candidates)",
              identical,
              f"baseline sum={[c['metrics'].get('sum') for c in b3]} entry_ids={b3[0]['entry_ids'] if b3 else None}; "
              f"scenario sum={[c['metrics'].get('sum') for c in s3]} entry_ids={s3[0]['entry_ids'] if s3 else None}"))
    conflicts = art["semantic_conflicts"]
    gk = [c for c in conflicts if c.get("raw_column") == "GEGENKONTO"]
    recorded = bool(gk) and any(c.get("semantic_role") == "entry_id_candidate"
                                or "VALIDATION INCOMPLETE" in (c.get("conflict") or "") for c in gk)
    r.append(("declared-vs-observed conflict recorded (GEGENKONTO alias quarantined)",
              recorded, f"GEGENKONTO conflict={[c.get('semantic_role') for c in gk]}"))
    # candidate/finding counts must not silently balloon
    r.append(("candidate count stable vs baseline (no silent wrong candidates)",
              len(art["candidates"]) == len(base["candidates"]),
              f"baseline={len(base['candidates'])} scenario={len(art['candidates'])}"))
    return r


def check_s4(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    # renamed original recorded as coverage gap
    orig = _mfiles(art).get(f"{BEGLEIT}/Stammdatenaenderungen_2025.csv", {})
    r.append(("original (renamed-away) file recorded as coverage failure",
              orig.get("parse_coverage") == "failed", f"coverage={orig.get('parse_coverage')}"))
    # renamed NEW file surfaced as unrecognized (manifest shows it)
    new = _mfiles(art).get(f"{BEGLEIT}/Masterdata_Changes_2025.csv", {})
    r.append(("renamed file surfaced in manifest as UNRECOGNIZED/unparsed",
              bool(new) and new.get("parse_coverage") == "failed",
              f"present={bool(new)} coverage={new.get('parse_coverage')}"))
    # R2 degrades to zero, no crash
    r2 = cands_by_rule(art, "R02")
    r.append(("R2 self-approval degrades to 0 candidates (no crash)", len(r2) == 0,
              f"R02 count={len(r2)} (baseline={len(cands_by_rule(base, 'R02'))})"))
    # no phantom absence claim about self-approval / master-data changes
    phantom = [f.get("finding_id") for f in art["findings"]
               if f.get("disposition") == "report"
               and ("no self-approval" in finding_text_blob(f)
                    or "no self approval" in finding_text_blob(f)
                    or "no master-data change" in finding_text_blob(f)
                    or "no masterdata change" in finding_text_blob(f)
                    or "keine selbstgenehmigung" in finding_text_blob(f))]
    r.append(("no phantom absence claim (e.g. 'no self-approval events exist')",
              not phantom, f"offending={phantom}"))
    return r


def check_s5(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    mf = _mfiles(art).get(f"{BEGLEIT}/Zahlungsexport_Bank_2025.csv", {})
    r.append(("unknown file listed as unparsed in manifest",
              bool(mf) and mf.get("parse_coverage") == "failed"
              and any("unrecognized" in e.lower() for e in mf.get("parser_errors", [])),
              f"present={bool(mf)} coverage={mf.get('parse_coverage')}"))
    # unknown file must not spawn candidates/findings (no phantom)
    r.append(("unknown file spawns no candidates (count == baseline)",
              len(art["candidates"]) == len(base["candidates"]),
              f"baseline={len(base['candidates'])} scenario={len(art['candidates'])}"))
    return r


def check_s6(base, art, rc, out):
    r = []
    r.append(("pipeline exits NONZERO (coverage failure)", rc != 0, f"exit={rc}"))
    r.append(("NO stack-trace spew (clean message, not a crash)",
              "Traceback (most recent call last)" not in out, ""))
    r.append(("clear A-class / coverage failure message present",
              "core_ledger_coverage" in out and "cannot be run" in out,
              "message names core_ledger_coverage"))
    r.append(("failure is at INGEST (fails fast, before finder)",
              "PIPELINE STOPPED: stage INGEST" in out, ""))
    return r


def check_s7(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    mf = _mfiles(art).get(f"{BEGLEIT}/Kreditlimitliste_Debitoren_2025.csv", {})
    # flagged as partial in manifest (non-default convention / encoding surfaced)
    flagged = bool(mf) and (mf.get("parse_coverage") == "partial"
                            or any("dot-decimal" in e or "encoding" in e.lower()
                                   for e in mf.get("parser_errors", [])))
    r.append(("decimal/encoding variant flagged in manifest (not silent)", flagged,
              f"coverage={mf.get('parse_coverage')} errors={mf.get('parser_errors')}"))
    # spot-check one value: parsed credit_limit must equal the true value (NOT ~100x)
    acct, true_v = _S7_STATE.get("account"), _S7_STATE.get("true_limit")
    parsed_v = _query_credit_limit(art["build"], acct)
    ok_val = (true_v is not None and parsed_v is not None
              and abs(parsed_v - true_v) < 0.01)
    r.append(("spot-check: amount parsed correctly (no silent ~100x inflation)", ok_val,
              f"account={acct} true={true_v} parsed={parsed_v}"))
    return r


def check_s8(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    mf = _mfiles(art).get("AV/Anlagenbuchungen.txt", {})
    r.append(("absent asset sub-ledger recorded as coverage failure (provided=False)",
              mf.get("parse_coverage") == "failed" and mf.get("provided") is False,
              f"coverage={mf.get('parse_coverage')} provided={mf.get('provided')}"))
    n = _table_rowcount(art["build"], "asset_tx")
    r.append(("asset_tx table exists but is empty (schema'd, no Binder Error)", n == 0,
              f"asset_tx rows={n}"))
    phantom = [f.get("finding_id") for f in art["findings"]
               if f.get("disposition") == "report"
               and ("no asset transaction" in finding_text_blob(f)
                    or "no fixed-asset transaction" in finding_text_blob(f)
                    or "keine anlagenbuchung" in finding_text_blob(f))]
    r.append(("no phantom absence claim about asset transactions", not phantom,
              f"offending={phantom}"))
    ev = art["eval"].get("scores", {})
    r.append(("missing sub-ledger introduced NO wrong findings (decoy FP 0, citations 100%)",
              ev.get("decoy_false_positives") == 0 and ev.get("citation_resolvability_pct") == 100.0,
              f"decoyFP={ev.get('decoy_false_positives')} citations={ev.get('citation_resolvability_pct')}"))
    return r


def check_s9(base, art, rc, out):
    r = []
    r.append(("pipeline completes (exit 0)", rc == 0, f"exit={rc}"))
    r.append(("no stack-trace spew", "Traceback (most recent call last)" not in out, ""))
    mf = _mfiles(art).get(f"{BEGLEIT}/OP-Liste_Kreditoren_2025.xlsx", {})
    r.append(("absent AP open-item list recorded as coverage failure (provided=False)",
              mf.get("parse_coverage") == "failed" and mf.get("provided") is False,
              f"coverage={mf.get('parse_coverage')} provided={mf.get('provided')}"))
    n = _table_rowcount(art["build"], "op_creditors_accounts")
    r.append(("op_creditors_accounts table exists but is empty (schema'd, no Binder Error)",
              n == 0, f"op_creditors_accounts rows={n}"))
    b4 = _bclass(art["build"], "ap_tieout_subledger_gl_op_tb")
    r.append(("AP tie-out degrades as a RECORDED deviation (not a crash)",
              b4 is not None and not b4.get("passed", True),
              f"ap tie-out deviation recorded={b4 is not None}"))
    phantom = [f.get("finding_id") for f in art["findings"]
               if f.get("disposition") == "report"
               and ("no open item" in finding_text_blob(f)
                    or "no ap open" in finding_text_blob(f)
                    or "keine offenen posten" in finding_text_blob(f))]
    r.append(("no phantom absence claim about AP open items", not phantom,
              f"offending={phantom}"))
    ev = art["eval"].get("scores", {})
    r.append(("missing OP list introduced NO wrong findings (decoy FP 0, citations 100%)",
              ev.get("decoy_false_positives") == 0 and ev.get("citation_resolvability_pct") == 100.0,
              f"decoyFP={ev.get('decoy_false_positives')} citations={ev.get('citation_resolvability_pct')}"))
    return r


def _query_credit_limit(build: Path, account):
    if not account:
        return None
    try:
        import duckdb
        con = duckdb.connect(str(build / "audit.duckdb"), read_only=True)
        row = con.execute(
            "SELECT credit_limit FROM credit_limits WHERE customer_account = ? LIMIT 1",
            [account]).fetchone()
        con.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _table_rowcount(build: Path, table: str):
    """Row count of a DuckDB table, or None if the table does not exist / errors.
    A non-None 0 proves the table was created (with schema) but empty — graceful
    degradation; a None would mean a missing-table Binder Error risk downstream."""
    try:
        import duckdb
        con = duckdb.connect(str(build / "audit.duckdb"), read_only=True)
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return None


def _bclass(build: Path, name: str):
    """Return the B-class property-test entry `name` from property_tests.json (or None)."""
    pt = _read_json(build / "property_tests.json") or {}
    for t in pt.get("b_class", []):
        if t.get("name") == name:
            return t
    return None


# --------------------------------------------------------------------- driver

SCENARIOS = [
    ("S1", "missing audit plan (delete Pruefungsplanung docx)", perturb_s1, check_s1),
    ("S2", "missing side table (delete Wareneingangsliste csv)", perturb_s2, check_s2),
    ("S3", "column-order shuffle in Sachkonten/index.xml", perturb_s3, check_s3),
    ("S4", "renamed file (Stammdatenaenderungen -> Masterdata_Changes)", perturb_s4, check_s4),
    ("S5", "extra unknown csv (untyped payments export)", perturb_s5, check_s5),
    ("S6", "empty GL (truncate Sachkontobuchungen.txt)", perturb_s6, check_s6),
    ("S7", "decimal/encoding variant (Kreditlimitliste UTF-8 dot decimals)", perturb_s7, check_s7),
    ("S8", "missing GDPdU sub-ledger (delete AV/Anlagenbuchungen.txt)", perturb_s8, check_s8),
    ("S9", "missing xlsx sidecar (delete OP-Liste_Kreditoren_2025.xlsx)", perturb_s9, check_s9),
]


def drift(base, art):
    ev_b, ev_s = base["eval"].get("scores", {}), art["eval"].get("scores", {})
    return {
        "candidates": f"{len(base['candidates'])} -> {len(art['candidates'])}",
        "findings": f"{len(base['findings'])} -> {len(art['findings'])}",
        "dispositions": f"{dispositions(base)} -> {dispositions(art)}",
        "eval_finding_recall": f"{ev_b.get('finding_recall')} -> {ev_s.get('finding_recall')}",
        "eval_decoy_fp": f"{ev_b.get('decoy_false_positives')} -> {ev_s.get('decoy_false_positives')}",
        "eval_citations_pct": f"{ev_b.get('citation_resolvability_pct')} -> {ev_s.get('citation_resolvability_pct')}",
    }


def ensure_baseline(rebuild: bool) -> dict:
    build = REPO / "build_rob_baseline"
    if rebuild or not (build / "findings.json").exists():
        print("[robust] building baseline (data/practice -> build_rob_baseline) ...", flush=True)
        rc, _ = run_pipeline(PRACTICE, build)
        if rc != 0:
            print(f"[robust] FATAL: baseline pipeline exited {rc}", file=sys.stderr)
            sys.exit(2)
    return load_artifacts(build)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", help="comma list e.g. S1,S3 (default: all)")
    ap.add_argument("--keep", action="store_true", help="keep data/robust_* and build_rob_* dirs")
    ap.add_argument("--rebuild-baseline", action="store_true")
    args = ap.parse_args(argv)

    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    base = ensure_baseline(args.rebuild_baseline)

    results = []
    for sid, title, perturb, check in SCENARIOS:
        if only and sid not in only:
            continue
        name = sid.lower()
        print(f"\n{'='*78}\n[{sid}] {title}\n{'='*78}", flush=True)
        dst = fresh_copy(name)
        try:
            perturb(dst)
        except Exception as exc:
            results.append({"sid": sid, "title": title, "passed": False,
                            "checks": [("perturbation applied", False, repr(exc))], "drift": {}})
            continue
        build = REPO / f"build_rob_{name}"
        rc, out = run_pipeline(dst, build)
        art = load_artifacts(build)
        checks = check(base, art, rc, out)
        passed = all(ok for _, ok, _ in checks)
        dr = drift(base, art) if rc == 0 else {"note": "pipeline stopped early (expected for S6)"}
        for cname, ok, detail in checks:
            print(f"   [{'PASS' if ok else 'FAIL'}] {cname}"
                  + (f"  ({detail})" if detail else ""), flush=True)
        results.append({"sid": sid, "title": title, "passed": passed,
                        "checks": checks, "drift": dr, "exit": rc})
        if not args.keep:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.rmtree(build, ignore_errors=True)

    # ---- summary table
    line = "=" * 78
    print(f"\n{line}\nROBUSTNESS SUMMARY\n{line}")
    print(f"{'id':<4} {'result':<6} scenario")
    for r in results:
        print(f"{r['sid']:<4} {'PASS' if r['passed'] else 'FAIL':<6} {r['title']}")
    print(line)
    print("FINDINGS-COUNT DRIFT vs baseline (baseline -> scenario):")
    for r in results:
        d = r["drift"]
        if "note" in d:
            print(f"  {r['sid']}: {d['note']}")
        else:
            print(f"  {r['sid']}: candidates {d['candidates']} | findings {d['findings']} | "
                  f"recall {d['eval_finding_recall']} | decoyFP {d['eval_decoy_fp']} | "
                  f"citations% {d['eval_citations_pct']}")
    n_pass = sum(r["passed"] for r in results)
    print(line)
    print(f"RESULT: {n_pass}/{len(results)} scenarios PASS")
    print(line)

    (REPO / "build_rob_report.json").write_text(
        json.dumps({"results": [{k: v for k, v in r.items() if k != "checks"}
                                 | {"checks": [{"name": n, "ok": o, "detail": d}
                                               for n, o, d in r["checks"]]}
                                 for r in results]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
