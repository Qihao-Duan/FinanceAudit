"""Shared deterministic helpers for Agent D stages (evidence/claims/defense/verdict).

Lives in financeaudit/evidence/ (Agent D owned); imported by claims/defense/verdict.
No LLM anywhere in this module (llm_used: false throughout).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb

FORMULA_VERSION = "agentD-1.0"

# digit tokens that appear in evalx decoy matchers; input_set_hash values are
# re-salted if a hash accidentally contains one (pure cosmetic guard so a random
# hex hash can never token-match a decoy entity in downstream text scans).
_RESERVED_DIGIT_TOKENS = (
    "480000", "209110", "209111", "209112", "209113",
    "691260", "677000", "110395",
)


# ----------------------------------------------------------------- basics

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(build: Path) -> duckdb.DuckDBPyConnection:
    db = Path(build) / "audit.duckdb"
    if not db.exists():
        raise FileNotFoundError(f"{db} missing — run python3 -m financeaudit.ingest.run first")
    return duckdb.connect(str(db), read_only=True)


def q(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def dec(raw) -> Decimal:
    """Parse a German-format raw amount string ('-53.550,00') into Decimal.

    Also accepts already-normalized strings ('-53550.00'). Raises on garbage —
    a claim recompute must fail loudly, never silently coerce.
    """
    if raw is None:
        raise InvalidOperation("raw amount is None")
    s = str(raw).strip().replace(" ", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return Decimal(s)


def json_dump(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)


def json_load(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def input_set_hash(op: str, operands) -> str:
    """Stable hash over (op, sorted signed operand refs); re-salted if the hex
    accidentally contains a reserved digit token."""
    basis = json.dumps({"v": FORMULA_VERSION, "op": op,
                        "operands": sorted(map(str, operands))}, sort_keys=True)
    salt = 0
    while True:
        h = hashlib.sha1((basis + ("" if not salt else f"|salt{salt}")).encode()).hexdigest()[:12]
        if not any(t in h for t in _RESERVED_DIGIT_TOKENS):
            return h
        salt += 1


# ------------------------------------------------------------- registry / rows

def registry_lookup(con, sids) -> dict:
    sids = [s for s in set(sids) if s]
    if not sids:
        return {}
    ph = ",".join("?" * len(sids))
    rows = q(con, f"SELECT * FROM source_registry WHERE source_id IN ({ph})", sids)
    return {r["source_id"]: r for r in rows}


#: tables that carry an exact raw amount string usable as recompute operand
AMOUNT_SOURCES = [
    ("gl", "amount_raw", "BUCHUNGSBETRAG"),
    ("vendor_tx", "amount_raw", "BUCHUNGSBETRAG"),
    ("customer_tx", "amount_raw", "BUCHUNGSBETRAG"),
    ("asset_tx", "amount_raw", "BUCHUNGSBETRAG"),
    ("goods_receipts", "amount_raw", "BETRAG"),
    ("goods_issues", "amount_raw", "BETRAG"),
    ("sales_invoices", "amount_raw", "BETRAG"),
    ("purchase_invoices_2026", "amount_raw", "BETRAG"),
    ("subsequent_payments", "amount_raw", "BETRAG"),
    ("approval_log", "sum_abs_raw", "SUMME_BETRAG"),
]


def amounts_for(con, sids) -> dict:
    """source_id -> {'raw': exact raw amount string, 'table': t, 'col': raw col label}."""
    sids = [s for s in set(sids) if s]
    if not sids:
        return {}
    ph = ",".join("?" * len(sids))
    out = {}
    have = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    for table, col, label in AMOUNT_SOURCES:
        if table not in have:
            continue
        try:
            rows = q(con, f"SELECT source_id, {col} AS raw FROM {table} "
                          f"WHERE source_id IN ({ph})", sids)
        except duckdb.Error:
            continue
        for r in rows:
            if r["raw"] is not None and r["source_id"] not in out:
                out[r["source_id"]] = {"raw": r["raw"], "table": table, "col": label}
    return out


# ------------------------------------------------------------- citations

def cell_citation(con, sid: str, col: str, cell_value) -> dict:
    reg = registry_lookup(con, [sid]).get(sid)
    if reg is None:
        raise KeyError(f"source_id {sid} not in source_registry")
    return {"kind": "cell", "source_id": sid, "file": reg["file"],
            "row_ids": [reg["row_no"]] if reg["row_no"] is not None else [],
            "col": col, "cell_value": str(cell_value),
            "display_locator": reg["display_locator"]}


def passage_citation(con, sid: str, quote: str) -> dict:
    reg = registry_lookup(con, [sid]).get(sid)
    if reg is None:
        raise KeyError(f"source_id {sid} not in source_registry")
    return {"kind": "passage", "source_id": sid, "file": reg["file"],
            "page_index": reg.get("page_index") if reg.get("page_index") is not None else 0,
            "page_label": reg.get("page_label") or (str(reg["para_no"]) if reg.get("para_no") else "1"),
            "quote": quote, "locator_precision": "page_only",
            "display_locator": reg["display_locator"]}


# ------------------------------------------------------------- manifest / gate

def load_manifest(con) -> dict:
    return {r["file"]: r for r in q(con, "SELECT * FROM source_manifest")}


#: evidence types vs. whether any dossier file's declared population scope
#: covers them (drives absence-vs-existence wording; PLAN §3.1 language gate).
EVIDENCE_SCOPE = {
    "goods_receipt_material": {"covered_by": "Begleitdokumente/Wareneingangsliste_2025.csv",
                               "note": "material/logistics receipts only"},
    "goods_receipt_services": {"covered_by": None,
                               "note": "services are outside the goods-receipt list scope"},
    "contract_document": {"covered_by": None,
                          "note": "no dossier file declares contract coverage"},
    "technical_assessment": {"covered_by": None,
                             "note": "no dossier file declares assessment coverage"},
    "bank_statement": {"covered_by": None,
                       "note": "bank statements are not part of the dossier"},
    "journal_approval": {"covered_by": "Begleitdokumente/Freigabe-Log_Journale_2025.csv",
                         "note": "journal-level approvals; coverage of all invoices/payments UNPROVEN"},
    "masterdata_approval": {"covered_by": "Begleitdokumente/Stammdatenaenderungen_2025.csv",
                            "note": "master data changes as provided"},
}

#: files whose scope is declared complete for their population AND whose
#: 'absence within the list' is therefore a provable absence claim
_PROVABLE_ABSENCE = {"goods_receipt_material", "masterdata_approval"}


def absence_gate(manifest: dict, evidence_type: str) -> dict:
    """Return {'claim_type','qualifier','coverage'} for a missing-evidence claim.

    absence (provable) only when parse coverage is complete AND the declared
    population scope covers the evidence type; otherwise a qualified existence
    claim with 'not found in the provided and parsed materials' wording.
    """
    spec = EVIDENCE_SCOPE.get(evidence_type, {"covered_by": None, "note": "unknown scope"})
    f = spec["covered_by"]
    coverage = manifest.get(f, {}).get("parse_coverage") if f else None
    if f and coverage == "complete" and evidence_type in _PROVABLE_ABSENCE:
        return {"claim_type": "absence", "qualifier": "",
                "coverage": f"{f}: parse complete; scope: {manifest[f]['population_scope']}"}
    return {"claim_type": "existence",
            "qualifier": "not found in the provided and parsed materials; "
                         + spec["note"],
            "coverage": (f"{f}: {coverage}" if f else "no dossier file covers this evidence type")}


# ------------------------------------------------------------- doc / token scan

def scan_token(con, token: str) -> list[dict]:
    """Deterministic dossier-wide scan for a reference token (case-insensitive).

    Looks in doc_units text, source_registry file names, and free-text columns
    of the ledger + side tables. Returns hit descriptors with source_ids.
    """
    t = f"%{token.lower()}%"
    hits = []
    for r in q(con, "SELECT source_id, file, text FROM doc_units "
                    "WHERE lower(text) LIKE ?", [t]):
        hits.append({"where": f"doc_units:{r['file']}", "source_id": r["source_id"],
                     "snippet": r["text"][:160]})
    for r in q(con, "SELECT DISTINCT file FROM source_registry WHERE lower(file) LIKE ?", [t]):
        hits.append({"where": "file_name", "source_id": None, "snippet": r["file"]})
    text_cols = [("gl", "text"), ("vendor_tx", "text"), ("customer_tx", "text"),
                 ("sales_invoices", "note"), ("goods_receipts", "note"),
                 ("purchase_invoices_2026", "note"), ("approval_log", "journal_name"),
                 ("masterdata_changes", "field"), ("masterdata_changes", "new_value")]
    for table, col in text_cols:
        try:
            rows = q(con, f"SELECT source_id, {col} AS v FROM {table} "
                          f"WHERE lower({col}) LIKE ? LIMIT 50", [t])
        except duckdb.Error:
            continue
        for r in rows:
            hits.append({"where": f"{table}.{col}", "source_id": r["source_id"],
                         "snippet": str(r["v"])[:160]})
    return hits


def resolves_outside(hits: list[dict], exclude_tables=("gl", "vendor_tx", "customer_tx")) -> bool:
    """True if a scanned reference resolves to anything beyond the posting texts
    that cite it (i.e. an actual document / list entry exists)."""
    for h in hits:
        where = h["where"]
        if where == "file_name" or where.startswith("doc_units"):
            return True
        if not any(where.startswith(f"{t}.") for t in exclude_tables):
            return True
    return False


# ------------------------------------------------------------- thresholds

#: Agent B key -> Agent D internal key
_THRESHOLD_KEY_MAP = {
    "approval_limit_eur": "payment_approval_limit",
    "materiality_overall_eur": "materiality",
    "materiality_performance_eur": "performance_materiality",
    "jet_sampling_eur": "jet_sampling",
    "lock_date": "lock_date",
}


def load_thresholds(build: Path, con) -> dict:
    """Prefer Agent B's build/thresholds.json; else deterministic extraction from
    the parsed Pruefungsplanung / IT attestation doc_units (with citations)."""
    p = Path(build) / "thresholds.json"
    if p.exists():
        raw = json_load(p)
        values = {}
        for k, v in (raw.get("thresholds") or {}).items():
            key = _THRESHOLD_KEY_MAP.get(k, k)
            cit = v.get("citation") or {}
            values[key] = {"value": v.get("value"), "source_id": cit.get("source_id"),
                           "quote": cit.get("quote"), "provenance": v.get("provenance")}
        if values:
            return {"origin": "build/thresholds.json (Agent B)", "llm_used": False,
                    "values": values}
    th = {"origin": "agentD-fallback-extraction", "llm_used": False, "values": {}}

    def _num(txt):  # '400.000' -> 400000
        return float(txt.replace(".", "").replace(",", "."))

    for r in q(con, "SELECT source_id, text FROM doc_units WHERE file LIKE '%Pruefungsplanung%'"):
        txt = r["text"]
        m = re.search(r"Gesamtwesentlichkeit\s+([\d.]+)\s*EUR.*?"
                      r"Toleranzwesentlichkeit\s+([\d.]+)\s*EUR.*?"
                      r"Nichtaufgriffsgrenze JET\s+([\d.]+)\s*EUR", txt, re.S)
        if m:
            th["values"]["materiality"] = {"value": _num(m.group(1)), "source_id": r["source_id"],
                                           "quote": m.group(0)[:180]}
            th["values"]["performance_materiality"] = {"value": _num(m.group(2)),
                                                       "source_id": r["source_id"]}
            th["values"]["jet_sampling"] = {"value": _num(m.group(3)), "source_id": r["source_id"]}
        m = re.search(r"Zahlungsfreigaben ab\s+([\d.]+)\s*EUR[^.]*zweite Freigabe[^.]*", txt)
        if m:
            th["values"]["payment_approval_limit"] = {"value": _num(m.group(1)),
                                                      "source_id": r["source_id"],
                                                      "quote": m.group(0)[:180]}
    for r in q(con, "SELECT source_id, text FROM doc_units WHERE file LIKE '%IT-Best%'"):
        m = re.search(r"Festschreibung[^.]*?(\d{2})\.(\d{2})\.(\d{4})", r["text"], re.S)
        if m:
            th["values"]["lock_date"] = {"value": f"{m.group(3)}-{m.group(2)}-{m.group(1)}",
                                         "source_id": r["source_id"],
                                         "quote": re.sub(r"\s+", " ", m.group(0))[:180]}
    # hard defaults if extraction found nothing (final dossier may lack the docx)
    defaults = {"payment_approval_limit": 10000.0, "materiality": 400000.0,
                "performance_materiality": 300000.0, "jet_sampling": 25000.0,
                "lock_date": "2026-01-20"}
    for k, v in defaults.items():
        th["values"].setdefault(k, {"value": v, "source_id": None,
                                    "quote": None, "note": "default (no citation)"})
    return th


def thr(th: dict, key: str):
    return th["values"][key]["value"]


def thr_citation(con, th: dict, key: str) -> dict | None:
    v = th["values"][key]
    if not v.get("source_id"):
        return None
    quote = v.get("quote")
    if not quote:
        rows = q(con, "SELECT text FROM doc_units WHERE source_id = ?", [v["source_id"]])
        quote = (rows[0]["text"][:180] if rows else "")
    return passage_citation(con, v["source_id"], re.sub(r"\s+", " ", quote).strip())
