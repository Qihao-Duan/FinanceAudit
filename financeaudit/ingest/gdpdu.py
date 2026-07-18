"""GDPdU txt parsing driven by each folder's index.xml.

Column NAMES come from the XML, but semantic roles are validated against the data
(see profiler.py — P0: this dossier's declared ERFASSUNGSNUMMER/JOURNALZEILE are empty
and the column declared GEGENKONTO actually holds journal-entry ids -> entry_id alias).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .common import (
    ManifestBuilder,
    SourceRegistry,
    parse_german_amount,
    parse_german_date,
    read_delimited,
    sha256_file,
)

GDPDU_DIRS = ["Sachkonten", "Debitoren", "Kreditoren", "AV"]


def read_index_columns(data_dir: Path):
    """Parse every index.xml -> {rel_txt_path: [declared column names]}."""
    declared = {}
    for d in GDPDU_DIRS:
        idx = data_dir / d / "index.xml"
        if not idx.exists():
            continue
        root = ET.parse(idx).getroot()
        for t in root.iter("Table"):
            url = t.findtext("URL")
            cols = [c.findtext("Name") for c in t.iter("VariableColumn")]
            declared[f"{d}/{url}"] = cols
    return declared


def _load_txt(data_dir, rel_path, registry: SourceRegistry, expected_ncols, errors):
    """Read a txt, register every row, return (file_hash, size, rows) with
    rows = [(row_no, fields, source_id)]."""
    path = data_dir / rel_path
    file_hash = sha256_file(path)
    size = path.stat().st_size
    parsed = []
    for line_no, fields, raw in read_delimited(path):
        if len(fields) != expected_ncols:
            errors.append(
                f"{rel_path}:row {line_no}: {len(fields)} fields, declared {expected_ncols}"
            )
        sid = registry.register(
            file=rel_path,
            file_hash=file_hash,
            kind="cell_row",
            locator=f"{rel_path}#row:{line_no}",
            content=raw,
            display_locator=f"{rel_path}:row {line_no}",
            row_no=line_no,
        )
        parsed.append((line_no, fields, sid))
    return file_hash, size, parsed


def parse_gdpdu(data_dir: Path, registry: SourceRegistry, manifest: ManifestBuilder,
                declared_counts: dict):
    """Parse the 8 txt files -> dict of table_name -> list[dict] + raw rows for profiling.

    declared_counts: {rel_path: expected_rows} from the Exportprotokoll (may be empty).
    """
    declared_cols = read_index_columns(data_dir)
    tables = {}
    raw_for_profile = {}  # rel_path -> (declared_col_names, [fields...])

    spec = {
        "Sachkonten/Sachkonten.txt": ("gl_accounts", _map_gl_account),
        "Sachkonten/Sachkontobuchungen.txt": ("gl", _map_gl),
        "Debitoren/Kunden.txt": ("customers", _map_customer),
        "Debitoren/Kundenbuchungen.txt": ("customer_tx", _map_customer_tx),
        "Kreditoren/Lieferanten.txt": ("vendors", _map_vendor),
        "Kreditoren/Lieferantenbuchungen.txt": ("vendor_tx", _map_vendor_tx),
        "AV/Anlagen.txt": ("assets", _map_asset),
        "AV/Anlagenbuchungen.txt": ("asset_tx", _map_asset_tx),
    }
    scopes = {
        "Sachkonten/Sachkonten.txt": "chart of accounts FY2025 (full export, Exportprotokoll-attested)",
        "Sachkonten/Sachkontobuchungen.txt": "complete FY2025 general-ledger journal incl. opening layer AB-2024 (Exportprotokoll-attested full export)",
        "Debitoren/Kunden.txt": "customer master data (full export, attested)",
        "Debitoren/Kundenbuchungen.txt": "customer subledger FY2025 (full export, attested)",
        "Kreditoren/Lieferanten.txt": "vendor master data (full export, attested)",
        "Kreditoren/Lieferantenbuchungen.txt": "vendor subledger FY2025 (full export, attested); BUCHUNGSART/STATUS are constant — payment identification must use GL BUCHUNGSTYP",
        "AV/Anlagen.txt": "asset master cards (full export, attested); no per-asset opening book values",
        "AV/Anlagenbuchungen.txt": "asset transactions FY2025 (full export, attested); account-level AfA rows + asset-level acquisitions/disposal",
    }

    # ROBUSTNESS: pre-create every expected GDPdU table (empty) so a skipped/absent file
    # still yields a DuckDB table (with its full schema via GDPDU_TABLE_SCHEMAS in write_table)
    # instead of a missing-table Binder Error downstream. A missing CORE ledger (gl) is caught
    # separately as a clean A-class coverage failure (proptests A1b core_ledger_coverage).
    for _rel, (tn, _m) in spec.items():
        tables.setdefault(tn, [])

    for rel_path, (table_name, mapper) in spec.items():
        cols = declared_cols.get(rel_path)
        errors = []
        if cols is None:
            manifest.add(file=rel_path, file_hash=None, size_bytes=None,
                         expected_units=None, parsed_units=0,
                         parser_errors=[f"no index.xml declaration for {rel_path}"],
                         population_scope=scopes.get(rel_path, ""), parse_coverage="failed")
            continue
        if not (data_dir / rel_path).exists():
            # ROBUSTNESS: an absent GDPdU txt (e.g. finals ships without the asset subledger)
            # degrades to a 'failed'-coverage manifest entry + empty table; dependent rules
            # (asset roll-forward, tie-outs) degrade with no phantom absence, pipeline continues.
            manifest.add(file=rel_path, file_hash=None, size_bytes=None,
                         expected_units=declared_counts.get(rel_path), parsed_units=0,
                         parser_errors=[f"{rel_path}: file not provided in this dossier"],
                         population_scope=scopes.get(rel_path, ""), parse_coverage="failed",
                         provided=False)
            continue
        file_hash, size, rows = _load_txt(data_dir, rel_path, registry, len(cols), errors)
        mapped = []
        for row_no, fields, sid in rows:
            fields = fields + [""] * (len(cols) - len(fields))  # tolerate short rows (flagged above)
            try:
                rec = mapper(fields)
            except Exception as exc:  # typing failure -> parser error (A-class test will fail)
                errors.append(f"{rel_path}:row {row_no}: {exc}")
                continue
            rec["row_id"] = row_no
            rec["source_id"] = sid
            mapped.append(rec)
        if table_name == "gl":  # line_no = running position within entry_id group
            counters = {}
            for rec in mapped:
                counters[rec["entry_id"]] = counters.get(rec["entry_id"], 0) + 1
                rec["line_no"] = counters[rec["entry_id"]]
        tables[table_name] = mapped
        raw_for_profile[rel_path] = (table_name, cols, [f for _, f, _ in rows])
        expected = declared_counts.get(rel_path, len(rows))
        manifest.add(file=rel_path, file_hash=file_hash, size_bytes=size,
                     expected_units=expected, parsed_units=len(mapped),
                     parser_errors=errors, population_scope=scopes.get(rel_path, ""))
    return tables, raw_for_profile


# ---------------------------------------------------------------- row mappers

def _amount(fields, i):
    val, raw, canon = parse_german_amount(fields[i])
    return {"amount": val, "amount_raw": raw, "amount_dec": canon}


def _map_gl(f):
    account = f[0].strip()
    hb, _, sub = account.partition("-")
    rec = {
        "entry_id": f[18].strip(),
        "line_no": None,  # filled after full pass
        "account": account,
        "hb_account": hb,
        "sub_account": sub or None,
        "currency": f[8].strip() or None,
        "text": f[10],
        "posting_date": parse_german_date(f[11]),
        "doc_date": parse_german_date(f[13]),
        "doc_ref": f[19].strip() or None,       # declared DOKUMENT — holds document refs
        "journal_ref": f[12].strip() or None,   # declared BUCHUNGSNUMMER
        "period_code": f[1].strip() or None,
        "posting_type": f[4].strip() or None,
        "user_id": f[20].strip() or None,
        "entry_date": parse_german_date(f[21]),
        "entry_time": f[22].strip() or None,
        "festschreibung": f[23].strip() or None,
        # unmapped raw columns kept verbatim
        "raw_steuerbuchungsreferenz": f[2].strip() or None,
        "raw_periodenzugehoerigkeit": f[3].strip() or None,
        "raw_korrektur": f[5].strip() or None,
        "raw_habenbuchung": f[6].strip() or None,
        "raw_buchungswert": f[9].strip() or None,
        "raw_belegnummer": f[14].strip() or None,
        "raw_spezialbuchung": f[15].strip() or None,
        "raw_erfassungsnummer": f[16].strip() or None,  # declared ERFASSUNGSNUMMER — empty (P0)
        "raw_journalzeile": f[17].strip() or None,      # declared JOURNALZEILE — empty (P0)
    }
    rec.update(_amount(f, 7))
    return rec


def _map_gl_account(f):
    return {
        "account": f[0].strip(),
        "name": f[1],
        "typ": f[2].strip() or None,
        "sperre": f[3].strip() or None,
        "exclusive_user": f[4].strip() or None,
        "benutzung": f[5].strip() or None,
        "kontenart": f[6].strip() or None,
    }


def _map_customer(f):
    return {
        "account": f[0].strip(), "ustid": f[1].strip() or None, "street": f[2],
        "plz": f[3].strip() or None, "city": f[4], "country": f[5].strip() or None,
        "name": f[6], "grp": f[7].strip() or None,
        "vat_grp": f[11].strip() or None, "currency": f[12].strip() or None,
        "raw_f1": f[8].strip() or None, "raw_f2": f[9].strip() or None,
        "raw_f3": f[10].strip() or None,
    }


def _map_vendor(f):
    return {
        "account": f[0].strip(), "ustid": f[1].strip() or None, "street": f[2],
        "plz": f[3].strip() or None, "city": f[4], "country": f[5].strip() or None,
        "name": f[6], "grp": f[7].strip() or None,
        "vat_grp": f[9].strip() or None, "currency": f[10].strip() or None,
        "raw_f1": f[8].strip() or None,
    }


def _map_customer_tx(f):
    # P0-analog misalignment: declared BELEGNUMMER (pos 4) is empty; the document
    # reference (AR5xxxxx/AB-2024/SG5xxxxx) lives in declared BUCHUNGSNUMMER (pos 2).
    rec = {
        "account": f[0].strip(),
        "posting_date": parse_german_date(f[4]),   # declared BUCHUNGSDATUM
        "doc_ref": f[1].strip() or None,           # semantic alias (declared BUCHUNGSNUMMER)
        "doc_date": parse_german_date(f[2]),       # declared BELEGDATUM
        "text": f[5],
        "currency": f[7].strip() or None,
        "module_tag": f[11].strip() or None,
        "status": None,
        "raw_belegnummer": f[3].strip() or None,
        "raw_buchungswert": f[8].strip() or None,
        "raw_letzter_ausgleichsbeleg": f[9].strip() or None,
        "raw_letzter_ausgleich": f[10].strip() or None,
    }
    rec.update(_amount(f, 6))
    return rec


def _map_vendor_tx(f):
    rec = {
        "account": f[0].strip(),
        "posting_date": parse_german_date(f[2]),   # declared BUCHUNGSDATUM
        "doc_ref": f[1].strip() or None,           # semantic alias (declared BUCHUNGSNUMMER)
        "doc_date": parse_german_date(f[4]),       # declared BELEGDATUM
        "text": f[5],
        "currency": f[7].strip() or None,
        "module_tag": f[11].strip() or None,
        "status": f[12].strip() or None,
        "raw_belegnummer": f[3].strip() or None,
        "raw_buchungswert": f[8].strip() or None,
        "raw_letzter_ausgleichsbeleg": f[9].strip() or None,
        "raw_letzter_ausgleich": f[10].strip() or None,
    }
    rec.update(_amount(f, 6))
    return rec


def _map_asset(f):
    return {
        "asset_id": f[0].strip(), "name": f[1], "grp": f[2].strip() or None,
        "typ": f[3].strip() or None, "status": f[8].strip() or None,
        "location": f[4].strip() or None, "main_asset": f[5].strip() or None,
        "manufacturer": f[6].strip() or None, "model": f[7].strip() or None,
    }


def _map_asset_tx(f):
    rec = {
        "target": f[0].strip(),
        "value_date": parse_german_date(f[1]),
        "doc_ref": f[2].strip() or None,
        "currency": f[4].strip() or None,
        "kind": f[6].strip() or None,   # Acquisition | Depreciation | Disposal
        "text": f[7],
        "grp": f[8].strip() or None,
        "level": f[9].strip() or None,
        "raw_buchungswert": f[5].strip() or None,
    }
    rec.update(_amount(f, 3))
    return rec


# ROBUSTNESS: full empty-table schema per GDPdU table, DERIVED from the mappers themselves
# (call each with padded-empty fields → the row-dict keys). Used by write_table when a table
# is empty/absent so downstream rules resolve every column (0 rows) instead of a Binder Error.
# Derived rather than hand-listed so it can never drift from the mappers. NOTE: this is the
# FULL column set (incl. raw_*/amount_dec), a superset of the ORDER hints in ingest/run.py.
def _derive_gdpdu_schema(mapper, nfields: int = 40):
    rec = mapper([""] * nfields)
    return ["row_id", "source_id"] + list(rec.keys())


GDPDU_TABLE_SCHEMAS = {
    "gl_accounts": _derive_gdpdu_schema(_map_gl_account),
    "gl": _derive_gdpdu_schema(_map_gl),
    "customers": _derive_gdpdu_schema(_map_customer),
    "customer_tx": _derive_gdpdu_schema(_map_customer_tx),
    "vendors": _derive_gdpdu_schema(_map_vendor),
    "vendor_tx": _derive_gdpdu_schema(_map_vendor_tx),
    "assets": _derive_gdpdu_schema(_map_asset),
    "asset_tx": _derive_gdpdu_schema(_map_asset_tx),
}
