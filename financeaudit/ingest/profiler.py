"""Per-column profiler: dtype / cardinality / null-rate / top-values / constant detection,
distribution-based semantic-role assignment and declared-vs-observed conflict recording.

P0 (PLAN §3.1): index.xml declared names are NOT trusted. The GL column declared
GEGENKONTO is promoted to semantic_role 'entry_id' only after passing three checks:
(a) 77xxxxx pattern share, (b) per-group zero-balance, (c) approval-log overlap.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal

_ROLE_PATTERNS = [
    ("date", re.compile(r"^\d{2}\.\d{2}\.\d{4}$")),
    ("time", re.compile(r"^\d{2}:\d{2}:\d{2}$")),
    ("entry_id", re.compile(r"^77\d{5}$")),
    ("user_id", re.compile(r"^(?:MV-U\d{2}|Admin)$")),
    ("account", re.compile(r"^\d{6}(?:-\d{6})?$")),
    ("doc_ref", re.compile(r"^(?:(?:AR|ER|WE|WA|AZ|BE|GJ|GB|SG)\d+|AB-\d{4}|SAMMEL-\d+|AfA)$")),
    ("amount", re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d+$|^-?\d+,\d+$")),
    ("integer", re.compile(r"^-?\d+$")),
]
_ROLE_DTYPE = {"date": "date", "time": "time", "amount": "number", "integer": "integer"}


def _classify(nonempty):
    for role, rx in _ROLE_PATTERNS:
        if sum(1 for v in nonempty if rx.match(v)) / len(nonempty) >= 0.95:
            return role
    return "text"


def _profile_column(file, table, position, declared, values):
    stripped = [str(v).strip() for v in values]
    nonempty = [v for v in stripped if v]
    null_rate = 1.0 - (len(nonempty) / len(stripped)) if stripped else 1.0
    uniq = set(nonempty)
    top = Counter(nonempty).most_common(5)
    if not nonempty:
        role, dtype = "empty", "empty"
    else:
        role = _classify(nonempty)
        dtype = _ROLE_DTYPE.get(role, "text")
    return {
        "file": file,
        "table_name": table,
        "raw_column": declared,
        "position": position,
        "declared_role": declared,
        "dtype": dtype,
        "n_unique": len(uniq),
        "null_rate": round(null_rate, 6),
        "top_values": json.dumps(top, ensure_ascii=False),
        "is_constant": bool(nonempty) and null_rate == 0.0 and len(uniq) == 1,
        "semantic_role": role,
        "conflict": None,
    }


def _col(rows, i):
    return [(r[i] if i < len(r) else "") for r in rows]


def _overlap(vals, ref_set):
    vs = {v for v in vals if v}
    if not vs:
        return 0.0, 0, 0
    inter = len(vs & ref_set)
    return inter / len(vs), inter, len(vs)


def profile_all(gdpdu_feed, sidecar_feed, cross):
    """gdpdu_feed: {rel_path: (table, declared_cols, raw_rows)};
    sidecar_feed: [(rel_path, table, headers, raw_rows)];
    cross: dict of reference sets for cross-table validation.
    Returns (profiles, conflicts)."""
    profiles = []
    by_key = {}

    def add(file, table, cols, rows):
        for i, name in enumerate(cols):
            p = _profile_column(file, table, i + 1, name, _col(rows, i))
            profiles.append(p)
            by_key[(file, name)] = p

    for rel, (table, cols, rows) in gdpdu_feed.items():
        add(rel, table, cols, rows)
    for rel, table, headers, rows in sidecar_feed:
        add(rel, table, headers, rows)

    conflicts = []

    def set_conflict(file, colname, semantic_role, text):
        p = by_key.get((file, colname))
        if p is None:
            return
        if semantic_role:
            p["semantic_role"] = semantic_role
        p["conflict"] = text
        conflicts.append({
            "file": file, "table": p["table_name"], "raw_column": colname,
            "declared_role": p["declared_role"], "semantic_role": p["semantic_role"],
            "conflict": text,
        })

    # ---- GL: validate the entry_id alias on the column declared GEGENKONTO
    gl_file = "Sachkonten/Sachkontobuchungen.txt"
    if gl_file in gdpdu_feed:
        _, cols, rows = gdpdu_feed[gl_file]
        gk = cols.index("GEGENKONTO")
        amt = cols.index("BUCHUNGSBETRAG")
        vals = _col(rows, gk)
        pat = re.compile(r"^77\d{5}$")
        share = sum(1 for v in vals if pat.match(v.strip())) / len(vals) if vals else 0.0
        groups = {}
        for r in rows:
            key = r[gk].strip()
            groups[key] = groups.get(key, Decimal(0)) + Decimal(
                r[amt].replace(".", "").replace(",", "."))
        unbalanced = sum(1 for s in groups.values() if s != 0)
        ap_ids = cross.get("approval_entry_ids", set())
        ap_hit = len(ap_ids & set(groups)) if ap_ids else 0
        checks_ok = share >= 0.99 and unbalanced == 0 and (not ap_ids or ap_hit == len(ap_ids))
        set_conflict(
            gl_file, "GEGENKONTO",
            "entry_id" if checks_ok else "entry_id_candidate",
            "declared GEGENKONTO (counter-account) but holds journal-entry ids: "
            f"77xxxxx pattern share {share:.4f}, {len(groups)} groups with {unbalanced} unbalanced, "
            f"approval-log overlap {ap_hit}/{len(ap_ids)}. Aliased to entry_id"
            + ("" if checks_ok else " (VALIDATION INCOMPLETE — kept as candidate)")
            + ". Consequence: NO usable counter-account column exists in this dossier.")
        set_conflict(
            gl_file, "ERFASSUNGSNUMMER", "empty",
            "declared ERFASSUNGSNUMMER (journal entry no.) but 100% empty; the journal-entry id "
            "actually lives in the column declared GEGENKONTO (see entry_id alias).")
        set_conflict(
            gl_file, "JOURNALZEILE", "empty",
            "declared JOURNALZEILE (journal line no.) but 100% empty; gl.line_no is derived as the "
            "running position within each entry_id group instead.")

    # ---- subledgers: declared BELEGNUMMER empty, doc refs live in declared BUCHUNGSNUMMER
    for rel, ref_key, ref_label in [
        ("Debitoren/Kundenbuchungen.txt", "sales_invoice_nos", "Fakturajournal_2025 invoice numbers"),
        ("Kreditoren/Lieferantenbuchungen.txt", "purchase_doc_refs", "Wareneingangsliste/Fakturajournal_2026 references"),
    ]:
        if rel not in gdpdu_feed:
            continue
        _, cols, rows = gdpdu_feed[rel]
        bn = cols.index("BUCHUNGSNUMMER")
        vals = [v.strip() for v in _col(rows, bn)]
        share, inter, n = _overlap(vals, cross.get(ref_key, set()))
        set_conflict(
            rel, "BUCHUNGSNUMMER", "doc_ref",
            "declared BUCHUNGSNUMMER but holds business document references "
            f"({inter}/{n} distinct values resolve against {ref_label}); aliased to doc_ref.")
        p = by_key.get((rel, "BELEGNUMMER"))
        if p and p["semantic_role"] == "empty":
            set_conflict(rel, "BELEGNUMMER", "empty",
                         "declared BELEGNUMMER (document no.) but 100% empty; document references "
                         "live in the column declared BUCHUNGSNUMMER.")
        for c in ("LETZTER_AUSGLEICHSBELEG", "LETZTER_AUSGLEICH"):
            p = by_key.get((rel, c))
            if p and p["semantic_role"] == "empty":
                set_conflict(rel, c, "empty",
                             f"declared {c} (clearing info) but 100% empty; payment matching must "
                             "run via GL BUCHUNGSTYP=='Zahlung', not via subledger clearing fields.")

    # ---- constant module-tag / status columns: record as negative-semantics note
    for rel, colname in [("Kreditoren/Lieferantenbuchungen.txt", "BUCHUNGSART"),
                         ("Kreditoren/Lieferantenbuchungen.txt", "STATUS"),
                         ("Debitoren/Kundenbuchungen.txt", "BUCHUNGSART")]:
        p = by_key.get((rel, colname))
        if p and p["is_constant"]:
            top = json.loads(p["top_values"])
            val = top[0][0] if top else "?"
            set_conflict(rel, colname, p["semantic_role"],
                         f"constant column (every row = {val!r}); cannot discriminate transaction "
                         "types — do not use for grouping or anomaly ranking (PLAN §3.1 P1).")

    return profiles, conflicts
