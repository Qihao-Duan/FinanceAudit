"""Parsers for the 19 Begleitdokumente (csv / xlsx / docx / pdf) -> side tables + doc_units."""
from __future__ import annotations

import csv
import re
from decimal import Decimal
from pathlib import Path

import docx as docx_lib
import fitz
import openpyxl

from . import common as _common
from .common import (
    ManifestBuilder,
    SourceRegistry,
    detect_decimal_convention,
    excel_amount,
    parse_amount_conv,
    parse_german_amount,
    parse_german_date,
    read_delimited,
    sha256_file,
)

BEGLEIT = "Begleitdokumente"

GDPDU_TXT_FILES = [
    "Sachkonten/Sachkonten.txt",
    "Sachkonten/Sachkontobuchungen.txt",
    "Debitoren/Kunden.txt",
    "Debitoren/Kundenbuchungen.txt",
    "Kreditoren/Lieferanten.txt",
    "Kreditoren/Lieferantenbuchungen.txt",
    "AV/Anlagen.txt",
    "AV/Anlagenbuchungen.txt",
]


# ------------------------------------------------------------- Exportprotokoll

def parse_exportprotokoll(data_dir: Path):
    """Extract declared (rows, sum, sha256) per GDPdU txt from the Exportprotokoll PDF.

    Returns ({rel_path: {declared_rows, declared_sum_raw, declared_sum_dec, declared_sha256}}, errors).
    Hash hex is split across lines in the PDF text layer -> concatenate hex-only lines.
    """
    path = data_dir / BEGLEIT / "Exportprotokoll_GDPdU_2025.pdf"
    entries, errors = {}, []
    if not path.exists():
        return entries, [f"{path.name} not found"]
    try:
        text = fitz.open(path)[0].get_text()
    except Exception as exc:
        return entries, [f"Exportprotokoll unreadable: {exc}"]
    positions = [(text.find(f), f) for f in GDPDU_TXT_FILES if text.find(f) >= 0]
    positions.sort()
    for (pos, f), nxt in zip(positions, positions[1:] + [(len(text), None)]):
        seg = text[pos + len(f): nxt[0]]
        lines = [ln.strip() for ln in seg.splitlines() if ln.strip()]
        try:
            declared_rows = int(lines[0])
            sum_raw = sum_dec = None
            rest = lines[1:]
            if rest and re.match(r"^-?[\d.]+,\d{2}$", rest[0]):
                sum_raw = rest[0]
                sum_dec = str(Decimal(sum_raw.replace(".", "").replace(",", ".")))
                rest = rest[1:]
            elif rest and rest[0] in ("–", "-", "—"):
                rest = rest[1:]
            hexcat = "".join(ln for ln in rest if re.fullmatch(r"[0-9a-f]+", ln))
            sha = hexcat[:64] if len(hexcat) >= 64 else None
            if sha is None:
                errors.append(f"Exportprotokoll: could not assemble sha256 for {f}")
            entries[f] = {
                "declared_rows": declared_rows,
                "declared_sum_raw": sum_raw,
                "declared_sum_dec": sum_dec,
                "declared_sha256": sha,
            }
        except Exception as exc:
            errors.append(f"Exportprotokoll: failed to parse block for {f}: {exc}")
    missing = [f for f in GDPDU_TXT_FILES if f not in entries]
    if missing:
        errors.append(f"Exportprotokoll: no entries found for {missing}")
    return entries, errors


# ------------------------------------------------------------------ CSV files

# target table -> (filename, [(target_col, source_header, type)]) type: text|amount|date|int
CSV_SPECS = {
    "goods_receipts": ("Wareneingangsliste_2025.csv", [
        ("we_no", "WARENEINGANG_NR", "text"), ("we_date", "WARENEINGANG_DATUM", "date"),
        ("invoice_ref", "RECHNUNGSNUMMER", "text"), ("vendor_account", "KREDITOR", "text"),
        ("vendor_name", "KREDITORNAME", "text"), ("amount", "BETRAG_EUR", "amount"),
        ("note", "BEMERKUNG", "text")]),
    "goods_issues": ("Warenausgangsliste_2025.csv", [
        ("wa_no", "WARENAUSGANG_NR", "text"), ("wa_date", "WARENAUSGANG_DATUM", "date"),
        ("invoice_ref", "RECHNUNGSNUMMER", "text"), ("customer_account", "DEBITOR", "text"),
        ("customer_name", "DEBITORNAME", "text"), ("amount", "BETRAG_EUR", "amount"),
        ("location", "LAGERORT", "text")]),
    "sales_invoices": ("Fakturajournal_2025.csv", [
        ("invoice_no", "RECHNUNGSNUMMER", "text"), ("kind", "ART", "text"),
        ("customer_account", "DEBITOR", "text"), ("customer_name", "DEBITORNAME", "text"),
        ("invoice_date", "FAKTURADATUM", "date"), ("service_date", "LEISTUNGSDATUM", "date"),
        ("amount", "BETRAG_EUR", "amount"), ("currency", "WAEHRUNG", "text"),
        ("note", "BEMERKUNG", "text")]),
    "purchase_invoices_2026": ("Fakturajournal_Januar_2026_Kreditoren.csv", [
        ("invoice_no", "RECHNUNGSNUMMER", "text"), ("kind", "ART", "text"),
        ("vendor_account", "KREDITOR", "text"), ("vendor_name", "KREDITORNAME", "text"),
        ("invoice_date", "FAKTURADATUM", "date"), ("service_date", "LEISTUNGSDATUM", "date"),
        ("amount", "BETRAG_EUR", "amount"), ("currency", "WAEHRUNG", "text"),
        ("note", "BEMERKUNG", "text")]),
    "subsequent_payments": ("Buchungen_Folgeperiode_2026.csv", [
        ("posting_date", "BUCHUNGSDATUM", "date"), ("doc_ref", "BELEG", "text"),
        ("customer_account", "DEBITOR", "text"), ("customer_name", "DEBITORNAME", "text"),
        ("amount", "BETRAG_EUR", "amount"), ("currency", "WAEHRUNG", "text"),
        ("offset_account", "GEGENKONTO_HB", "text"), ("text", "BUCHUNGSTEXT", "text")]),
    "approval_log": ("Freigabe-Log_Journale_2025.csv", [
        ("entry_id", "ERFASSUNGSNUMMER", "text"), ("journal_name", "JOURNALNAME", "text"),
        ("n_lines", "ANZAHL_ZEILEN", "int"), ("sum_abs", "SUMME_ABS_EUR", "amount"),
        ("creator", "ERSTELLER", "text"), ("created_date", "ERFASST_AM", "date"),
        ("created_time", "ERFASST_UM", "text"), ("approver", "FREIGEBER", "text"),
        ("approval_date", "FREIGABEDATUM", "date"), ("status", "FREIGABESTATUS", "text")]),
    "masterdata_changes": ("Stammdatenaenderungen_2025.csv", [
        ("change_date", "DATUM", "date"), ("kind", "ART", "text"),
        ("account", "KONTO", "text"), ("name", "NAME", "text"), ("field", "FELD", "text"),
        ("old_value", "WERT_ALT", "text"), ("new_value", "WERT_NEU", "text"),
        ("changed_by", "GEAENDERT_VON", "text"), ("approved_by", "GENEHMIGT_VON", "text"),
        ("approved", "GENEHMIGT", "text")]),
    "credit_limits": ("Kreditlimitliste_Debitoren_2025.csv", [
        ("customer_account", "DEBITOR", "text"), ("customer_name", "DEBITORNAME", "text"),
        ("credit_limit", "KREDITLIMIT_EUR", "amount"),
        ("utilization", "AUSNUTZUNG_31_12_2025_EUR", "amount"),
        ("status", "STATUS", "text"), ("collateral", "BESICHERUNG", "text")]),
    "shareholders": ("Gesellschafterliste_Beteiligungen.csv", [
        ("name", "NAME", "text"), ("share_pct", "ANTEIL_PROZENT", "amount"),
        ("ic_flag", "IC_KENNZEICHEN", "text"), ("relation", "VERHAELTNIS", "text"),
        ("note", "BEMERKUNG", "text")]),
}

# Optional synthetic-test evidence. Absence is normal and does not create a
# failed-coverage row; when present, rows enter the same source registry and
# typed DuckDB contract as the official sidecars.
OPTIONAL_CSV_SPECS = {
    "batch_approvals": ("Batchfreigaben_Synthetisch.csv", [
        ("batch_id", "BATCH_ID", "text"), ("approval_date", "DATUM", "date"),
        ("vendor_account", "KREDITOR", "text"),
        ("invoice_ref", "RECHNUNG", "text"),
        ("payment_count", "TEILZAHLUNGEN", "int"),
        ("total_amount", "GESAMT_EUR", "amount"),
        ("creator", "ERSTELLER", "text"), ("approver", "FREIGEBER", "text"),
        ("status", "STATUS", "text")]),
    "bank_payment_details": ("Bankwechsel_Zahlungsdetails_Synthetisch.csv", [
        ("change_id", "WECHSEL_ID", "text"),
        ("change_date", "AENDERUNGSDATUM", "date"),
        ("vendor_account", "KREDITOR", "text"),
        ("old_iban", "ALTE_IBAN", "text"), ("new_iban", "NEUE_IBAN", "text"),
        ("payment_date", "ZAHLUNGSDATUM", "date"),
        ("payment_ref", "ZAHLUNGSREFERENZ", "text"),
        ("invoice_ref", "RECHNUNG", "text"), ("entry_id", "ENTRY_ID", "text"),
        ("amount", "BETRAG_EUR", "amount"), ("used_iban", "VERWENDETE_IBAN", "text")]),
    "accrual_schedule": ("Abgrenzungsnachweis_Synthetisch.csv", [
        ("schedule_id", "NACHWEIS_ID", "text"),
        ("closing_date", "ABSCHLUSSDATUM", "date"),
        ("accrual_entry_id", "ACCRUAL_ENTRY_ID", "text"),
        ("accrual_ref", "ACCRUAL_REFERENZ", "text"),
        ("invoice_ref", "RECHNUNG", "text"), ("vendor_account", "KREDITOR", "text"),
        ("service_date", "LEISTUNGSDATUM", "date"),
        ("invoice_date", "RECHNUNGSDATUM", "date"),
        ("obligation_amount", "VERPFLICHTUNG_EUR", "amount"),
        ("allocated_amount", "ZUGEORDNET_EUR", "amount"),
        ("status", "STATUS", "text"), ("description", "BESCHREIBUNG", "text")]),
    "technical_assessments": ("Technische_Beurteilungen_Synthetisch.csv", [
        ("assessment_id", "BEURTEILUNG_ID", "text"),
        ("assessment_date", "DATUM", "date"), ("asset_id", "ANLAGE", "text"),
        ("invoice_ref", "RECHNUNG", "text"), ("description", "BEZEICHNUNG", "text"),
        ("assessor", "TECHNISCHER_BEURTEILER", "text"),
        ("assessment", "BEFUND", "text"),
        ("capacity_before", "KAPAZITAET_VORHER", "int"),
        ("capacity_after", "KAPAZITAET_NACHHER", "int"),
        ("useful_life_extension_years", "NUTZUNGSDAUER_PLUS_JAHRE", "int"),
        ("approver", "FREIGEBER", "text"), ("status", "STATUS", "text")]),
}

CSV_SCOPES = {
    "goods_receipts": "material/logistics goods receipts only — services are NOT covered by this list",
    "goods_issues": "delivery-related goods issues, 1:1 with FY2025 sales invoices; credit notes legitimately have no issue",
    "sales_invoices": "sales invoice journal FY2025 incl. 1 credit note (kind column)",
    "purchase_invoices_2026": "post-period vendor invoices January 2026 as provided (8 invoices)",
    "subsequent_payments": "customer receipts ONLY — not a complete post-period general ledger; no non-receipt inference permitted",
    "approval_log": "journal-level approvals; coverage of all invoices/payments UNPROVEN — absence of a journal here is not evidence of missing approval",
    "masterdata_changes": "master data changes 2025 as provided",
    "credit_limits": "customer credit limits as of 31.12.2025",
    "shareholders": "shareholders and affiliated companies incl. section header lines (see is_section_header)",
    "batch_approvals": "synthetic test fixture: aggregate authorization for component payments",
    "bank_payment_details": "synthetic test fixture: bank-master change linked to payment destination",
    "accrual_schedule": "synthetic test fixture: year-end accrual allocation to subsequent invoices",
    "technical_assessments": "synthetic test fixture: pre-posting technical support for capitalization",
}


def _csv_schema(colmap, extra=()):
    """Full ordered column list a CSV table carries once populated (row dict keys).
    Amount columns expand to <tgt>, <tgt>_raw, <tgt>_dec (see _parse_csv_table)."""
    cols = ["row_id", "source_id"]
    for tgt, _src, typ in colmap:
        cols.append(tgt)
        if typ == "amount":
            cols += [tgt + "_raw", tgt + "_dec"]
    cols += list(extra)
    return cols


# ROBUSTNESS (S2/S4): an ABSENT or empty CSV must still create a DuckDB table with its
# FULL declared schema, so rules that SELECT its columns return 0 rows instead of raising
# a Binder Error (column not found) that would crash the Finder. Populated tables get their
# schema from the row dicts as before — this map is only consulted for the empty case.
CSV_TABLE_SCHEMAS = {
    table: _csv_schema(spec[1],
                       extra=(("section", "is_section_header") if table == "shareholders" else ()))
    for table, spec in CSV_SPECS.items()
}
CSV_TABLE_SCHEMAS.update({
    table: _csv_schema(spec[1]) for table, spec in OPTIONAL_CSV_SPECS.items()
})


# ------------------------------------------------- Finals control-file adapters
#
# The finals dossier (Beispiel Dämmstoffe GmbH) ships several HIGH-VALUE control
# files under RENAMED names, in ISO-8859/cp1252 encoding, and one of them carries
# an unquoted internal ';' inside a free-text column (BEMERKUNG). The builtin
# CSV_SPECS above match fixed practice filenames, so these files were previously
# UNPARSED (parse_coverage=failed). The deterministic adapters below register every
# row into the source registry, emit typed tables + a source_manifest entry, and
# feed the column profiler — mirroring the _parse_csv_table contract. They are
# OPTIONAL in the OPTIONAL_CSV_SPECS sense: absent in the practice dossier is normal
# (empty schema'd table, no 'failed' manifest row), present in finals is parsed.
#
# Filename matching is prefix-tolerant (normalized: lower-cased, umlauts folded,
# '-'/'_' unified) so a renamed variant like 'Aenderungsprotokoll_2025_erweitert.csv'
# still binds. Each spec is parsed POSITIONALLY against a fixed column layout; the
# header row is validated by name (drift -> recorded parser error, never a crash).
#
# Column types: text | date | amount | int (same vocabulary as CSV_SPECS). Amount
# columns expand to <tgt>, <tgt>_raw, <tgt>_dec exactly like _parse_csv_table.

# base names present in THIS dossier (added to KNOWN_BEGLEIT_FILES so the LLM
# auto-adapter / unrecognized-file lister skip them); variant suffixes are also
# tolerated at parse time via prefix matching + a runtime KNOWN-set update.
FINALS_KNOWN_FILES = {
    "Aenderungsprotokoll_2025.csv",
    "Stammdatenaenderungen_Debitoren_2025.csv",
    "Stammdaten-Statusliste_2025.csv",
    "Rechtsfaelle_Insolvenzen.csv",
    "Kontenplan-Mapping.csv",
}

FINALS_CSV_SPECS = [
    {
        # HIGH VALUE: changes to already-FINALIZED (festgeschrieben) GL entries.
        # Every BEMERKUNG holds an unquoted internal ';' -> free_index cap-split.
        "table": "change_protocol",
        "match": ("aenderungsprotokoll",),
        "ncols": 9,
        "free_index": 7,  # BEMERKUNG (0-based); 1 fixed column (FESTSCHREIBUNG) to its right
        "cols": [
            ("entry_no", "BUCHUNGSNUMMER", "text"),
            ("account", "SACHKONTO", "text"),
            ("posting_date", "BUCHUNGSDATUM", "date"),
            ("change_type", "AENDERUNGSART", "text"),
            ("user_id", "BENUTZER", "text"),
            ("changed_date", "GEAENDERT_AM", "date"),
            ("changed_time", "GEAENDERT_UM", "text"),
            ("note", "BEMERKUNG", "text"),
            ("finalized_before_change", "FESTSCHREIBUNG_VOR_AENDERUNG", "text"),
        ],
        "scope": "change protocol of already-finalized GL entries FY2025 "
                 "(Stornobuchung/Generalstorno etc.); finalized_before_change='Ja' marks a "
                 "change to a festgeschriebene entry — high-value immutability control",
    },
    {
        # Appends to the canonical masterdata_changes shape (kind='Debitor').
        "table": "masterdata_changes",
        "append": True,
        "match": ("stammdatenaenderungen_debitoren",),
        "ncols": 9,
        "free_index": None,
        "const": {"kind": "Debitor"},
        "cols": [
            ("change_date", "DATUM", "date"),
            ("account", "DEBITOR", "text"),
            ("name", "DEBITORNAME", "text"),
            ("field", "FELD", "text"),
            ("old_value", "WERT_ALT", "text"),
            ("new_value", "WERT_NEU", "text"),
            ("changed_by", "GEAENDERT_VON", "text"),
            ("approved_by", "GENEHMIGT_VON", "text"),
            ("approved", "GENEHMIGT", "text"),
        ],
        "scope": "debtor (Debitoren) master-data changes 2025 as provided; appended to "
                 "masterdata_changes with kind='Debitor'",
    },
    {
        "table": "masterdata_status",
        "match": ("stammdaten_statusliste",),
        "ncols": 7,
        "free_index": None,
        "cols": [
            ("account", "KONTONUMMER", "text"),
            ("kind", "ART", "text"),
            ("name", "NAME", "text"),
            ("status", "STATUS", "text"),
            ("blocked_date", "GESPERRT_AM", "date"),
            ("deleted_date", "GELOESCHT_AM", "date"),
            ("cpd_flag", "CPD_KENNZEICHEN", "text"),
        ],
        "scope": "debtor/creditor master-data status list 2025 "
                 "(Aktiv/Gesperrt/Geloescht, block & delete dates, CPD flag)",
    },
    {
        "table": "legal_cases",
        "match": ("rechtsfaelle_insolvenzen", "rechtsfaelle"),
        "ncols": 6,
        "free_index": None,
        "cols": [
            ("account", "DEBITOR", "text"),
            ("name", "DEBITORNAME", "text"),
            ("date", "DATUM", "date"),
            ("status", "STATUS", "text"),
            ("claim_amount", "FORDERUNG_EUR", "amount"),
            ("note", "BEMERKUNG", "text"),
        ],
        "scope": "legal cases / insolvencies against debtors as provided (incl. a "
                 "monitoring statement row asserting no open proceedings)",
    },
    {
        # Large reference table (ISO-8859): journal-format account code -> main account.
        "table": "account_map",
        "match": ("kontenplan_mapping", "kontenplan"),
        "ncols": 4,
        "free_index": None,
        "cols": [
            ("journal_account", "KONTO_JOURNALFORMAT", "text"),
            ("main_account", "HAUPTKONTO", "text"),
            ("account_name", "KONTOBEZEICHNUNG", "text"),
            ("n_postings", "ANZAHL_BUCHUNGEN", "int"),
        ],
        "scope": "chart-of-accounts mapping: journal-format account code -> 6-digit main "
                 "account with per-code posting counts (reference/lookup table)",
    },
]

# Empty-table schemas (mirrors CSV_TABLE_SCHEMAS) so an ABSENT finals file still
# yields a 0-row DuckDB table WITH its columns (no Binder Error). The append spec
# (masterdata_changes) reuses the canonical schema already registered above.
CSV_TABLE_SCHEMAS.update({
    s["table"]: _csv_schema(s["cols"], extra=tuple(s.get("const", {}).keys()))
    for s in FINALS_CSV_SPECS if not s.get("append")
})


def _finals_norm(name: str) -> str:
    """Lower-case, fold German umlauts/ß, unify '-'/'_' — for tolerant prefix matching."""
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s.replace("-", "_")


def _read_finals_csv(path: Path):
    """Read a ;-separated finals CSV, trying cp1252 -> latin-1 (never fails, covers
    ISO-8859) -> utf-8-sig. Returns (encoding_used, [(line_no_1based, raw_line), ...])."""
    for enc in ("cp1252", "latin-1", "utf-8-sig"):
        try:
            with open(path, encoding=enc, newline="") as fh:
                text = fh.read()
            used = enc
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 above never raises
        with open(path, encoding="latin-1", newline="") as fh:
            text = fh.read()
        used = "latin-1"
    return used, list(enumerate(text.splitlines(), start=1))


def _split_finals_row(raw: str, ncols: int, free_index):
    """Split a ;-line into exactly `ncols` fields.

    Well-formed rows (<= ncols separators) are parsed with csv.reader so any quoted
    fields are honored. When `free_index` is set and the row OVERFLOWS (an unquoted
    internal ';' inside the free-text column at `free_index`), the fixed columns on
    the left and right are taken from the ends and everything in between is rejoined
    — 'parse from the right / cap the split' (Aenderungsprotokoll BEMERKUNG)."""
    parts = raw.split(";")
    if free_index is None or len(parts) <= ncols:
        fields = next(csv.reader([raw], delimiter=";", quotechar='"'))
        if len(fields) < ncols:
            fields = fields + [""] * (ncols - len(fields))
        return fields
    right = ncols - 1 - free_index
    left = parts[:free_index]
    tail = parts[len(parts) - right:] if right else []
    middle = ";".join(parts[free_index:len(parts) - right])
    return left + [middle] + tail


def _find_finals_file(data_dir: Path, patterns):
    """Return the first .csv under Begleitdokumente whose normalized stem starts with
    any of `patterns` (already normalized), else None."""
    beg = data_dir / BEGLEIT
    if not beg.is_dir():
        return None
    for p in sorted(beg.iterdir()):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() != ".csv":
            continue
        stem = _finals_norm(p.stem)
        if any(stem.startswith(m) for m in patterns):
            return p
    return None


def _parse_finals_spec(data_dir, spec, registry, manifest):
    """Parse one finals control file per `spec`. Returns (rows, feed_or_None, claimed_name_or_None).
    rows == [] with claimed_name None when no matching file is present (absence is normal)."""
    path = _find_finals_file(data_dir, spec["match"])
    if path is None:
        return [], None, None
    rel = f"{BEGLEIT}/{path.name}"
    table = spec["table"]
    ncols, free_index, cols = spec["ncols"], spec.get("free_index"), spec["cols"]
    const = spec.get("const", {})
    file_hash = sha256_file(path)
    enc, lines = _read_finals_csv(path)
    errors = []
    if enc != _common.ENCODING:
        errors.append(f"{rel}: decoded as {enc} (cp1252 failed) — non-default encoding")
    data_lines = [(ln, raw) for ln, raw in lines[1:] if raw.strip()]
    # header validation (positional parse; names only checked to surface layout drift)
    if lines:
        header = _split_finals_row(lines[0][1], ncols, free_index)
        for i, (_tgt, src, _typ) in enumerate(cols):
            got = header[i].strip() if i < len(header) else ""
            if got != src:
                errors.append(f"{rel}: column {i} header {got!r} != expected {src!r}")
    # per-amount-column decimal-convention inference (ROBUSTNESS S7)
    split_cache = [_split_finals_row(raw, ncols, free_index) for _, raw in data_lines]
    amount_conv = {}
    for i, (tgt, _src, typ) in enumerate(cols):
        if typ == "amount":
            conv = detect_decimal_convention(
                [f[i] if i < len(f) else "" for f in split_cache])
            amount_conv[tgt] = conv
            if conv != "de":
                errors.append(f"{rel}: column {tgt!r} parsed as dot-decimal "
                              "(non-German convention detected) — flagged for review")
    rows = []
    for (ln, raw), fields in zip(data_lines, split_cache):
        sid = registry.register(
            file=rel, file_hash=file_hash, kind="cell_row",
            locator=f"{rel}#row:{ln}", content=raw,
            display_locator=f"{rel}:row {ln}", row_no=ln)
        rec = {"row_id": ln, "source_id": sid}
        rec.update(const)
        for i, (tgt, _src, typ) in enumerate(cols):
            v = fields[i] if i < len(fields) else ""
            try:
                if typ == "date":
                    rec[tgt] = parse_german_date(v)
                elif typ == "amount":
                    val, raw_a, canon = parse_amount_conv(v, amount_conv.get(tgt, "de"))
                    rec[tgt] = val
                    rec[tgt + "_raw"] = raw_a
                    rec[tgt + "_dec"] = canon
                elif typ == "int":
                    rec[tgt] = int(v) if v.strip() else None
                else:
                    rec[tgt] = v.strip() or None
            except Exception as exc:
                errors.append(f"{rel}:row {ln}:{tgt}: {exc}")
                rec[tgt] = None
        rows.append(rec)
    manifest.add(file=rel, file_hash=file_hash, size_bytes=path.stat().st_size,
                 expected_units=len(data_lines), parsed_units=len(rows),
                 parser_errors=errors, population_scope=spec["scope"])
    # keep the LLM auto-adapter / unrecognized-lister from re-processing this file
    KNOWN_BEGLEIT_FILES.add(path.name)
    feed = (rel, table, [src for _, src, _ in cols], split_cache)
    return rows, feed, path.name


def parse_finals_sidecars(data_dir, registry, manifest):
    """Parse all finals control files. Returns (new_tables, append_rows, feed_list, claimed).

    new_tables: {table: rows} for non-append specs (key always present, [] if absent).
    append_rows: {table: rows} for append specs (only when a file matched).
    feed_list: profiler feed tuples for matched files.
    claimed: set of consumed filenames."""
    new_tables, append_rows, feed_list, claimed = {}, {}, [], set()
    for spec in FINALS_CSV_SPECS:
        rows, feed, name = _parse_finals_spec(data_dir, spec, registry, manifest)
        if feed is not None:
            feed_list.append(feed)
        if name is not None:
            claimed.add(name)
        if spec.get("append"):
            if rows:
                append_rows.setdefault(spec["table"], []).extend(rows)
        else:
            new_tables[spec["table"]] = rows
    return new_tables, append_rows, feed_list, claimed


def _parse_csv_table(data_dir, table, spec, registry, manifest):
    fname, colmap = spec
    rel = f"{BEGLEIT}/{fname}"
    path = data_dir / rel
    # ROBUSTNESS (S2 missing-side-table / S4 renamed file): a declared sidecar that is
    # absent must degrade to an empty table + a 'failed' coverage manifest entry, NOT a
    # FileNotFoundError crash. Downstream rules gate on parse_coverage / observability
    # (e.g. R15 goods-receipt obligation, R19 sequence integrity), so an empty table
    # produces no phantom absence claims.
    if not path.exists():
        manifest.add(file=rel, file_hash=None, size_bytes=None, expected_units=None,
                     parsed_units=0, parser_errors=[f"{rel}: file not provided in this dossier"],
                     parse_coverage="failed", population_scope=CSV_SCOPES.get(table, ""),
                     provided=False)
        return [], (rel, [h for _, h, _ in colmap], [])
    file_hash = sha256_file(path)
    lines = read_delimited(path)
    encoding_used = _common.LAST_READ_ENCODING
    header = [h.strip() for h in lines[0][1]]
    errors = []
    if encoding_used != _common.ENCODING:
        errors.append(f"{rel}: decoded as {encoding_used} (cp1252 failed) — non-default encoding")
    idx = {}
    for tgt, src, typ in colmap:
        if src in header:
            idx[tgt] = (header.index(src), typ)
        else:
            errors.append(f"{rel}: declared header {src!r} not found (headers={header})")
    # ROBUSTNESS (S7 decimal variant): infer each amount column's decimal convention from
    # its own values. Practice columns are German -> 'de' (identical output); a dot-decimal
    # finals column is parsed correctly instead of being silently inflated ~100x.
    amount_conv = {}
    for tgt, (i, typ) in idx.items():
        if typ == "amount":
            col_vals = [(f[i] if i < len(f) else "") for _, f, _ in lines[1:]]
            conv = detect_decimal_convention(col_vals)
            amount_conv[tgt] = conv
            if conv != "de":
                errors.append(f"{rel}: column {tgt!r} parsed as dot-decimal (non-German "
                              "convention detected) — flagged for review")
    rows = []
    section = None
    n_nonempty = 0
    for line_no, fields, raw in lines[1:]:
        if not any(x.strip() for x in fields):
            continue  # pure separator line (counted separately)
        n_nonempty += 1
        sid = registry.register(
            file=rel, file_hash=file_hash, kind="cell_row",
            locator=f"{rel}#row:{line_no}", content=raw,
            display_locator=f"{rel}:row {line_no}", row_no=line_no)
        rec = {"row_id": line_no, "source_id": sid}
        for tgt, (i, typ) in idx.items():
            v = fields[i] if i < len(fields) else ""
            try:
                if typ == "date":
                    rec[tgt] = parse_german_date(v)
                elif typ == "amount":
                    val, raw_a, canon = parse_amount_conv(v, amount_conv.get(tgt, "de"))
                    rec[tgt] = val
                    rec[tgt + "_raw"] = raw_a
                    rec[tgt + "_dec"] = canon
                elif typ == "int":
                    rec[tgt] = int(v) if v.strip() else None
                else:
                    rec[tgt] = v.strip() or None
            except Exception as exc:
                errors.append(f"{rel}:row {line_no}:{tgt}: {exc}")
                rec[tgt] = None
        if table == "shareholders":
            only_name = rec.get("name") and not any(
                rec.get(k) for k in ("share_pct", "ic_flag", "relation", "note"))
            if only_name:
                section = rec["name"]
            rec["section"] = section
            rec["is_section_header"] = bool(only_name)
        rows.append(rec)
    manifest.add(file=rel, file_hash=file_hash, size_bytes=path.stat().st_size,
                 expected_units=n_nonempty, parsed_units=len(rows), parser_errors=errors,
                 population_scope=CSV_SCOPES.get(table, ""))
    raw_rows = [f for _, f, _ in lines[1:] if any(x.strip() for x in f)]
    return rows, (rel, header, raw_rows)


# ----------------------------------------------------------------- XLSX files

def _xl_register(registry, rel, file_hash, sheet, row_no, values):
    content = ";".join("" if v is None else str(v) for v in values)
    return registry.register(
        file=rel, file_hash=file_hash, kind="cell_row",
        locator=f"{rel}#sheet:{sheet}#row:{row_no}", content=content,
        display_locator=f"{rel}:{sheet}:row {row_no}", row_no=row_no)


# ROBUSTNESS: empty-table schemas for the xlsx side tables (not in the GDPdU ORDER map),
# so an ABSENT xlsx yields a 0-row DuckDB table WITH its columns rather than a Binder Error
# when a rule SELECTs from it (mirrors CSV_TABLE_SCHEMAS; see write_table in ingest/run.py).
XLSX_TABLE_SCHEMAS = {
    "permissions": ["row_id", "source_id", "user_id", "department", "can_post", "can_approve",
                    "can_pay", "can_create_vendor", "can_periods", "is_admin", "is_mgmt", "note"],
    "op_debitors_accounts": ["row_id", "source_id", "account", "name", "grp",
                             "balance", "balance_raw", "balance_dec"],
    "op_creditors_accounts": ["row_id", "source_id", "account", "name", "grp",
                              "balance", "balance_raw", "balance_dec"],
    "op_debitors_items": ["row_id", "source_id", "account", "name", "doc_ref", "doc_date",
                          "amount", "amount_raw", "amount_dec"],
    "trial_balance": ["row_id", "source_id", "account", "name", "kind", "opening", "opening_dec",
                      "debit", "debit_dec", "credit", "credit_dec", "closing", "closing_dec",
                      "closing_computed"],
    "reconciliation": ["row_id", "source_id", "label", "value", "value_raw", "value_dec", "file"],
}


def _xlsx_absent(manifest, rel, scope):
    """ROBUSTNESS: record a missing xlsx sidecar as a 'failed'-coverage manifest entry (its
    table stays empty-but-schema'd, downstream tie-outs/rules degrade, no phantom absence)."""
    manifest.add(file=rel, file_hash=None, size_bytes=None, expected_units=None,
                 parsed_units=0, parser_errors=[f"{rel}: file not provided in this dossier"],
                 parse_coverage="failed", population_scope=scope + " — NOT PROVIDED",
                 provided=False)


def parse_xlsx_files(data_dir: Path, registry, manifest):
    tables = {"permissions": [], "op_debitors_accounts": [], "op_debitors_items": [],
              "op_creditors_accounts": [], "trial_balance": [], "reconciliation": []}
    profile_feed = []

    # --- Berechtigungsauswertung (header sheet-row 3, 9 users)
    rel = f"{BEGLEIT}/Berechtigungsauswertung_2025.xlsx"
    path = data_dir / rel
    perm_scope = "permission matrix of the 9 posting-capable users per 31.12.2025 (SoD basis)"
    if not path.exists():
        _xlsx_absent(manifest, rel, perm_scope)
    else:
        fh = sha256_file(path)
        ws = openpyxl.load_workbook(path, data_only=True).active
        errors, parsed = [], 0
        raw_rows = []
        for r_no, row in enumerate(ws.iter_rows(min_row=4, values_only=True), start=4):
            if row[0] is None:
                continue
            sid = _xl_register(registry, rel, fh, ws.title, r_no, row)
            tables["permissions"].append({
                "user_id": str(row[0]), "department": row[1],
                "can_post": row[2] == "X", "can_approve": row[3] == "X",
                "can_pay": row[4] == "X", "can_create_vendor": row[5] == "X",
                "can_periods": row[6] == "X", "is_admin": row[7] == "X",
                "is_mgmt": (str(row[8]).strip().lower() == "ja") if row[8] else False,
                "note": row[9], "row_id": r_no, "source_id": sid})
            raw_rows.append([("" if v is None else str(v)) for v in row])
            parsed += 1
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=parsed, parsed_units=parsed, parser_errors=errors,
                     population_scope=perm_scope)
        profile_feed.append((rel, "permissions",
                             ["Benutzer", "Abteilung", "Buchen", "Journal freigeben", "Zahlungslauf",
                              "Stammdaten/Kreditor anlegen", "Perioden", "Systemadmin", "Management",
                              "Bemerkung"], raw_rows))

    # --- OP-Liste Debitoren (2 sheets) / Kreditoren (1 sheet)
    for rel_name, acc_table, item_table, scope in [
        ("OP-Liste_Debitoren_2025.xlsx", "op_debitors_accounts", "op_debitors_items",
         "AR open items per 31.12.2025; account balances complete, item sheet is an excerpt (Auszug) of current-year items"),
        ("OP-Liste_Kreditoren_2025.xlsx", "op_creditors_accounts", None,
         "AP open account balances per 31.12.2025"),
    ]:
        rel = f"{BEGLEIT}/{rel_name}"
        path = data_dir / rel
        if not path.exists():
            _xlsx_absent(manifest, rel, scope)
            continue
        fh = sha256_file(path)
        wb = openpyxl.load_workbook(path, data_only=True)
        errors, parsed = [], 0
        ws = wb.worksheets[0]
        for r_no, row in enumerate(ws.iter_rows(min_row=4, values_only=True), start=4):
            if row[0] is None:
                continue
            sid = _xl_register(registry, rel, fh, ws.title, r_no, row)
            g = lambda i: row[i] if len(row) > i else None
            try:
                val, raw_a, canon = excel_amount(g(3))
            except Exception as exc:
                # finals-robustness: a row whose expected amount cell does not
                # parse (different layout, subtotal/text row) is skipped and
                # RECORDED — never crash the whole ingest (finals run 2026-07-18).
                errors.append(f"{ws.title} row {r_no}: amount cell not parseable "
                              f"({str(exc)[:60]}; value={g(3)!r})")
                continue
            tables[acc_table].append({
                "account": str(g(0)), "name": g(1), "grp": g(2),
                "balance": val, "balance_raw": raw_a, "balance_dec": canon,
                "row_id": r_no, "source_id": sid})
            parsed += 1
        if item_table and len(wb.worksheets) > 1:
            ws2 = wb.worksheets[1]
            for r_no, row in enumerate(ws2.iter_rows(min_row=4, values_only=True), start=4):
                if row[0] is None:
                    continue
                sid = _xl_register(registry, rel, fh, ws2.title, r_no, row)
                g2 = lambda i: row[i] if len(row) > i else None
                try:
                    val, raw_a, canon = excel_amount(g2(4))
                    ddate = parse_german_date(g2(3)) if g2(3) is not None else None
                except Exception as exc:
                    errors.append(f"{ws2.title} row {r_no}: cell not parseable "
                                  f"({str(exc)[:60]}; amount={g2(4)!r} date={g2(3)!r})")
                    continue
                tables[item_table].append({
                    "account": str(g2(0)), "name": g2(1), "doc_ref": g2(2),
                    "doc_date": ddate,
                    "amount": val, "amount_raw": raw_a, "amount_dec": canon,
                    "row_id": r_no, "source_id": sid})
                parsed += 1
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=parsed + len(errors), parsed_units=parsed,
                     parser_errors=errors, population_scope=scope)

    # --- Saldenliste 2025 (trial balance; closing column is an uncached formula =D+E-F)
    rel = f"{BEGLEIT}/Saldenliste_2025.xlsx"
    path = data_dir / rel
    tb_scope = "trial balance FY2025 (43 accounts) + net-income reconciliation sheet"
    if not path.exists():
        _xlsx_absent(manifest, rel, tb_scope)
    else:
        fh = sha256_file(path)
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb["Saldenliste 2025"]
        errors, parsed = [], 0
        for r_no, row in enumerate(ws.iter_rows(min_row=5, values_only=True), start=5):
            if row[0] is None:
                continue  # skips the formula-only 'Summe' row
            sid = _xl_register(registry, rel, fh, ws.title, r_no, row)
            op, op_raw, op_dec = excel_amount(row[3])
            de, de_raw, de_dec = excel_amount(row[4])
            cr, cr_raw, cr_dec = excel_amount(row[5])
            closing_dec = str(Decimal(op_dec or "0") + Decimal(de_dec or "0") - Decimal(cr_dec or "0"))
            tables["trial_balance"].append({
                "account": str(row[0]), "name": row[1], "kind": row[2],
                "opening": op, "opening_dec": op_dec, "debit": de, "debit_dec": de_dec,
                "credit": cr, "credit_dec": cr_dec,
                "closing": float(Decimal(closing_dec)), "closing_dec": closing_dec,
                "closing_computed": True,  # file stores formula =D+E-F without cached value
                "row_id": r_no, "source_id": sid})
            parsed += 1
        # second sheet: Überleitung -> reconciliation rows
        if "Überleitung JA 2025" in wb.sheetnames:
            ws2 = wb["Überleitung JA 2025"]
            for r_no, row in enumerate(ws2.iter_rows(values_only=True), start=1):
                label = next((v for v in row if isinstance(v, str) and v.strip()), None)
                if not label:
                    continue
                value = next((v for v in row if isinstance(v, (int, float))), None)
                sid = _xl_register(registry, rel, fh, ws2.title, r_no, row)
                val, raw_a, canon = excel_amount(value)
                tables["reconciliation"].append({
                    "label": label, "value": val, "value_raw": raw_a, "value_dec": canon,
                    "file": rel, "row_id": r_no, "source_id": sid})
                parsed += 1
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=parsed, parsed_units=parsed,
                     parser_errors=["closing column has no cached values; computed as opening+debit-credit per the stored formula =D+E-F"],
                     parse_coverage="complete", population_scope=tb_scope)

    # --- Saldenliste 2024 (prior year) — structurally empty
    rel = f"{BEGLEIT}/Saldenliste_2024_Vorjahr.xlsx"
    path = data_dir / rel
    if not path.exists():
        _xlsx_absent(manifest, rel, "prior-year trial balance")
    else:
        fh = sha256_file(path)
        ws = openpyxl.load_workbook(path, data_only=True).active
        n_data = sum(1 for row in ws.iter_rows(min_row=4, values_only=True) if any(v is not None for v in row))
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=n_data, parsed_units=n_data, parser_errors=[],
                     population_scope="prior-year trial balance — EMPTY (0 data rows): no comparative-year data provided")

    # --- Abstimmung Nebenbücher/HB -> reconciliation
    rel = f"{BEGLEIT}/Abstimmung_Nebenbuecher_HB_2025.xlsx"
    path = data_dir / rel
    recon_scope = "management-prepared subledger/GL reconciliation per 31.12.2025 (zero differences claimed)"
    if not path.exists():
        _xlsx_absent(manifest, rel, recon_scope)
    else:
        fh = sha256_file(path)
        ws = openpyxl.load_workbook(path, data_only=True).active
        parsed = 0
        for r_no, row in enumerate(ws.iter_rows(values_only=True), start=1):
            label = next((v for v in row if isinstance(v, str) and v.strip()), None)
            if not label:
                continue
            value = next((v for v in row if isinstance(v, (int, float))), None)
            sid = _xl_register(registry, rel, fh, ws.title, r_no, row)
            val, raw_a, canon = excel_amount(value)
            tables["reconciliation"].append({
                "label": label, "value": val, "value_raw": raw_a, "value_dec": canon,
                "file": rel, "row_id": r_no, "source_id": sid})
            parsed += 1
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=parsed, parsed_units=parsed, parser_errors=[],
                     population_scope=recon_scope)

    return tables, profile_feed


# ------------------------------------------------------------- docx/pdf units

def parse_doc_units(data_dir: Path, registry, manifest):
    doc_units = []

    # docx: paragraphs + table rows
    rel = f"{BEGLEIT}/Pruefungsplanung_JET_2025.docx"
    path = data_dir / rel
    # ROBUSTNESS (S1 missing-audit-plan): a missing audit-plan docx must not crash ingest.
    # It degrades to a 'failed' manifest entry with zero doc_units; the threshold extractor
    # then finds no doc-sourced values and falls back to config defaults (thresholds.py).
    if not path.exists():
        manifest.add(file=rel, file_hash=None, size_bytes=None, expected_units=None,
                     parsed_units=0, parser_errors=[f"{rel}: file not provided in this dossier"],
                     parse_coverage="failed", provided=False,
                     population_scope="normative source: JET audit planning — NOT PROVIDED "
                     "(thresholds fall back to config defaults)")
        return _parse_pdf_units(data_dir, registry, manifest, doc_units)
    fh = sha256_file(path)
    d = docx_lib.Document(path)
    parsed = 0
    for p_no, para in enumerate(d.paragraphs, start=1):
        if not para.text.strip():
            continue
        sid = registry.register(
            file=rel, file_hash=fh, kind="para",
            locator=f"{rel}#para:{p_no}", content=para.text,
            display_locator=f"{rel}:para {p_no}", para_no=p_no)
        doc_units.append({"source_id": sid, "file": rel, "page_index": None,
                          "page_label": None, "para_no": p_no,
                          "unit_type": "para", "text": para.text})
        parsed += 1
    for t_no, tbl in enumerate(d.tables, start=1):
        for r_no, trow in enumerate(tbl.rows, start=1):
            text = " | ".join(c.text for c in trow.cells)
            sid = registry.register(
                file=rel, file_hash=fh, kind="table",
                locator=f"{rel}#table:{t_no}#row:{r_no}", content=text,
                display_locator=f"{rel}:table {t_no}:row {r_no}", row_no=r_no)
            doc_units.append({"source_id": sid, "file": rel, "page_index": None,
                              "page_label": None, "para_no": None,
                              "unit_type": "table_row", "text": text})
            parsed += 1
    manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                 expected_units=parsed, parsed_units=parsed, parser_errors=[],
                 population_scope="normative source: JET audit planning (materiality 400k/300k, JET sampling threshold 25k, payment dual-approval limit 10k, selection criteria K1-K7)")

    return _parse_pdf_units(data_dir, registry, manifest, doc_units)


def _parse_pdf_units(data_dir: Path, registry, manifest, doc_units):
    """PDF text extraction (one 'page' registry unit per page + one 'para' doc_unit per
    text block). ROBUSTNESS (S1 and finals): a missing PDF degrades to a 'failed' manifest
    entry rather than crashing ingest."""
    pdf_scopes = {
        "Exportprotokoll_GDPdU_2025.pdf": "export protocol: declared row counts, amount sums and SHA-256 for the 8 GDPdU txt files",
        "IT-Bestaetigung_Vollstaendigkeit_2025.pdf": "IT completeness/immutability attestation (Soll=Haben=345,350,778.06; Festschreibung 2026-01-20)",
        "JA-Entwurf_2025_Auszug_Bilanz_GuV.pdf": "DRAFT financial statements extract (balance sheet + P&L) — audit object, unaudited",
    }
    for fname, scope in pdf_scopes.items():
        rel = f"{BEGLEIT}/{fname}"
        path = data_dir / rel
        if not path.exists():
            manifest.add(file=rel, file_hash=None, size_bytes=None, expected_units=None,
                         parsed_units=0, parser_errors=[f"{rel}: file not provided in this dossier"],
                         parse_coverage="failed", population_scope=scope + " — NOT PROVIDED",
                         provided=False)
            continue
        fh = sha256_file(path)
        pdf = fitz.open(path)
        parsed = 0
        for p_idx in range(len(pdf)):
            page = pdf[p_idx]
            label = str(p_idx + 1)
            registry.register(
                file=rel, file_hash=fh, kind="page",
                locator=f"{rel}#page:{p_idx}", content=page.get_text(),
                display_locator=f"{rel}:page {label}",
                page_index=p_idx, page_label=label)
            parsed += 1
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            for b_no, b in enumerate(blocks, start=1):
                text = b[4].strip()
                sid = registry.register(
                    file=rel, file_hash=fh, kind="para",
                    locator=f"{rel}#page:{p_idx}#block:{b_no}", content=text,
                    display_locator=f"{rel}:page {label}:para {b_no}",
                    page_index=p_idx, page_label=label, para_no=b_no)
                doc_units.append({"source_id": sid, "file": rel, "page_index": p_idx,
                                  "page_label": label, "para_no": b_no,
                                  "unit_type": "para", "text": text})
        manifest.add(file=rel, file_hash=fh, size_bytes=path.stat().st_size,
                     expected_units=len(pdf), parsed_units=parsed, parser_errors=[],
                     population_scope=scope)
    return doc_units


def parse_sidecars(data_dir: Path, registry: SourceRegistry, manifest: ManifestBuilder):
    """Parse all 19 Begleitdokumente. Returns (tables, doc_units, csv_profile_feed)."""
    tables = {}
    profile_feed = []
    for table, spec in CSV_SPECS.items():
        rows, feed = _parse_csv_table(data_dir, table, spec, registry, manifest)
        tables[table] = rows
        profile_feed.append((feed[0], table, feed[1], feed[2]))
    for table, spec in OPTIONAL_CSV_SPECS.items():
        fname, _ = spec
        if (data_dir / BEGLEIT / fname).exists():
            rows, feed = _parse_csv_table(data_dir, table, spec, registry, manifest)
            profile_feed.append((feed[0], table, feed[1], feed[2]))
        else:
            rows = []
        tables[table] = rows
    xlsx_tables, xlsx_feed = parse_xlsx_files(data_dir, registry, manifest)
    tables.update(xlsx_tables)
    profile_feed.extend(xlsx_feed)
    # Finals control-file adapters (renamed / ISO-8859 / semicolon-in-BEMERKUNG files).
    finals_new, finals_append, finals_feed, finals_claimed = parse_finals_sidecars(
        data_dir, registry, manifest)
    tables.update(finals_new)
    for tname, trows in finals_append.items():
        tables.setdefault(tname, []).extend(trows)
    profile_feed.extend(finals_feed)
    doc_units = parse_doc_units(data_dir, registry, manifest)
    # Steuercodes: empty directory in the export
    st_dir = data_dir / "Steuercodes"
    if st_dir.exists() and not any(st_dir.iterdir()):
        manifest.add(file="Steuercodes/", file_hash=None, size_bytes=0,
                     expected_units=0, parsed_units=0, parser_errors=[],
                     population_scope="empty directory in the export — no tax-code tables provided")
    _list_unrecognized_begleit(data_dir, manifest, finals_claimed)
    return tables, doc_units, profile_feed


# Files this ingest knows how to parse (used only to surface UNRECOGNIZED extras).
KNOWN_BEGLEIT_FILES = (
    {fname for fname, _ in CSV_SPECS.values()}
    | {fname for fname, _ in OPTIONAL_CSV_SPECS.values()}
    | set(FINALS_KNOWN_FILES)
    | {"Berechtigungsauswertung_2025.xlsx", "OP-Liste_Debitoren_2025.xlsx",
       "OP-Liste_Kreditoren_2025.xlsx", "Saldenliste_2025.xlsx",
       "Saldenliste_2024_Vorjahr.xlsx", "Abstimmung_Nebenbuecher_HB_2025.xlsx"}
    | {"Pruefungsplanung_JET_2025.docx"}
    | {"Exportprotokoll_GDPdU_2025.pdf", "IT-Bestaetigung_Vollstaendigkeit_2025.pdf",
       "JA-Entwurf_2025_Auszug_Bilanz_GuV.pdf"}
)


def _list_unrecognized_begleit(data_dir: Path, manifest: ManifestBuilder, claimed=()):
    """ROBUSTNESS (S5 extra unknown file / S4 renamed file): surface any Begleitdokument
    that no adapter maps to as an explicit 'failed'-coverage manifest entry, so a finals
    file that was renamed or added is VISIBLE (coverage gap) rather than silently ignored —
    which would otherwise risk a phantom absence claim. No table/citation is produced."""
    beg = data_dir / BEGLEIT
    if not beg.is_dir():
        return
    claimed = set(claimed)
    for p in sorted(beg.iterdir()):
        if (not p.is_file() or p.name.startswith(".")
                or p.name in KNOWN_BEGLEIT_FILES or p.name in claimed):
            continue
        manifest.add(file=f"{BEGLEIT}/{p.name}", file_hash=sha256_file(p),
                     size_bytes=p.stat().st_size, expected_units=None, parsed_units=0,
                     parser_errors=[f"{p.name}: unrecognized file — no adapter maps to it "
                                    "(present but UNPARSED; listed for coverage visibility)"],
                     parse_coverage="failed",
                     population_scope="UNRECOGNIZED file — not mapped to any parser; no "
                     "absence/existence claim may rely on its (non-)contents")
