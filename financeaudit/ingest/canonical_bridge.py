"""Canonical bridge — route auto-adapter (ext_*) tables and extended journals
into the canonical tables the deterministic rules read (integrator, finals
2026-07-18).

The auto-adapter (LLM Mapper) structures unknown/renamed side files into
`ext_*` tables with clean columns, but the finder rules read fixed canonical
tables (sales_invoices, trial_balance, op_*_accounts, masterdata_changes). If
the builtin adapter yielded zero rows for a canonical table AND a semantically
matching ext_ table exists, bridge it — deterministically, by column-name
matching. Runs in-process during ingest so a full re-run reproduces it.

Also promotes an EXTENDED sales journal (a Fakturajournal_*_erweitert with a
WARENAUSGANG_NR column) directly into sales_invoices, carrying the goods-issue
flag + remark that the revenue-timing check needs.
"""
from __future__ import annotations

from typing import Dict, List


def _amount(row: dict, *keys):
    for k in keys:
        v = row.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            try:
                return float(v.replace(".", "").replace(",", "."))
            except ValueError:
                pass
    return None


def _first(row: dict, *keys):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


# ext_ table -> (canonical table, column-mapping fn producing canonical row)
def _map_invoice_journal(r: dict) -> dict:
    return {
        "invoice_no": _first(r, "invoice_number", "invoice_no", "RECHNUNGSNUMMER"),
        "kind": _first(r, "document_type", "kind", "ART") or "Rechnung",
        "customer_account": _first(r, "customer_account", "DEBITOR"),
        "customer_name": _first(r, "customer_name", "DEBITORNAME"),
        "invoice_date": _first(r, "invoice_date", "FAKTURADATUM"),
        "service_date": _first(r, "service_date", "LEISTUNGSDATUM"),
        "amount": _amount(r, "amount_eur", "amount", "BETRAG_EUR"),
        "amount_raw": _first(r, "amount_eur_raw", "amount_raw"),
        "amount_dec": _first(r, "amount_eur_dec", "amount_dec"),
        "currency": _first(r, "currency", "WAEHRUNG") or "EUR",
        "note": _first(r, "remark", "BEMERKUNG", "note"),
        "goods_issue_ref": _first(r, "warenausgang_nr", "WARENAUSGANG_NR"),
        "source_id": r.get("source_id"),
    }


def _dec_of(r: dict, *keys):
    """Canonical decimal shadow value (string) that property tests read via
    r['*_dec']. Prefer an existing _dec column; else stringify the numeric."""
    for k in keys:
        v = r.get(k)
        if v not in (None, ""):
            return str(v)
    amt = _amount(r, *keys)
    return None if amt is None else repr(amt)


def _map_trial_balance(r: dict) -> dict:
    return {
        "account": _first(r, "account"),
        "name": _first(r, "account_name", "name"),
        "kind": _first(r, "account_type", "kind"),
        "opening": _amount(r, "opening_balance_2025_01_01", "opening"),
        "opening_dec": _dec_of(r, "opening_balance_2025_01_01_dec", "opening_dec",
                               "opening_balance_2025_01_01", "opening"),
        "debit": _amount(r, "debit_2025", "debit"),
        "debit_dec": _dec_of(r, "debit_2025_dec", "debit_dec", "debit_2025", "debit"),
        "credit": _amount(r, "credit_2025", "credit"),
        "credit_dec": _dec_of(r, "credit_2025_dec", "credit_dec", "credit_2025", "credit"),
        "closing": _amount(r, "closing_balance_2025_12_31", "closing"),
        "closing_dec": _dec_of(r, "closing_balance_2025_12_31_dec", "closing_dec",
                               "closing_balance_2025_12_31", "closing"),
        "closing_computed": False,
        "source_id": r.get("source_id"),
    }


def _map_vendor_open(rows: List[dict]) -> List[dict]:
    agg: Dict[str, dict] = {}
    for r in rows:
        acc = _first(r, "vendor_account")
        if acc is None:
            continue
        a = agg.setdefault(acc, {"account": acc,
                                 "name": _first(r, "vendor_name"),
                                 "grp": _first(r, "vendor_group"),
                                 "balance": 0.0,
                                 "source_id": r.get("source_id")})
        amt = _amount(r, "amount_eur", "amount")
        if amt is not None:
            a["balance"] = round(a["balance"] + amt, 2)
    for a in agg.values():
        a["balance_dec"] = repr(a["balance"]); a["balance_raw"] = None
    return list(agg.values())


def _map_debtor_open(rows: List[dict]) -> List[dict]:
    agg: Dict[str, dict] = {}
    for r in rows:
        acc = _first(r, "customer_account", "debtor_account")
        if acc is None:
            continue
        a = agg.setdefault(acc, {"account": acc,
                                 "name": _first(r, "customer_name", "debtor_name"),
                                 "grp": _first(r, "customer_group"),
                                 "balance": 0.0,
                                 "source_id": r.get("source_id")})
        amt = _amount(r, "amount_eur", "amount")
        if amt is not None:
            a["balance"] = round(a["balance"] + amt, 2)
    for a in agg.values():
        a["balance_dec"] = repr(a["balance"]); a["balance_raw"] = None
    return list(agg.values())


def _map_masterdata(r: dict) -> dict:
    return {
        "change_date": _first(r, "change_date", "DATUM"),
        "kind": "Debitor",
        "account": _first(r, "customer_account", "DEBITOR", "account"),
        "name": _first(r, "customer_name", "DEBITORNAME", "name"),
        "field": _first(r, "changed_field", "FELD", "field"),
        "old_value": _first(r, "old_value", "WERT_ALT"),
        "new_value": _first(r, "new_value", "WERT_NEU"),
        "changed_by": _first(r, "changed_by", "GEAENDERT_VON"),
        "approved_by": _first(r, "approved_by", "GENEHMIGT_VON"),
        "approved": _first(r, "approved", "GENEHMIGT"),
        "source_id": r.get("source_id"),
    }


def parse_extended_invoice_journal(data_dir, registry) -> List[dict]:
    """Directly parse a Fakturajournal_*_erweitert.csv (extended sales journal
    with a WARENAUSGANG_NR goods-issue column) into canonical sales_invoices
    rows, registering each row's source_id. Returns [] if no such file.

    This file is the authoritative in-year revenue source; the LLM Mapper may
    map only the small next-period Januar file, so we parse the extended one
    deterministically."""
    import csv
    from pathlib import Path
    beg = Path(data_dir) / "Begleitdokumente"
    if not beg.is_dir():
        return []
    cand = sorted(p for p in beg.glob("Fakturajournal_*erweitert*.csv"))
    if not cand:
        return []
    path = cand[0]
    rel = f"Begleitdokumente/{path.name}"
    text = None
    for enc in ("cp1252", "latin-1", "utf-8-sig"):
        try:
            text = path.read_text(encoding=enc)
            break
        except Exception:
            continue
    if text is None:
        return []
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    if not rows:
        return []
    hdr = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(hdr)}

    def g(r, k):
        i = idx.get(k)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    from .common import sha256_file
    try:
        fh = sha256_file(path)
    except Exception:
        import hashlib
        fh = hashlib.sha1(rel.encode()).hexdigest()
    for rn, r in enumerate(rows[1:], start=2):
        if not r or not g(r, "RECHNUNGSNUMMER"):
            continue
        content = ";".join(r)
        sid = registry.register(
            file=rel, file_hash=fh, kind="cell_row",
            locator=f"{rel}#row:{rn}", content=content,
            display_locator=f"{rel}:row {rn}", row_no=rn)
        raw = g(r, "BETRAG_EUR")
        try:
            amt = float(raw.replace(".", "").replace(",", ".")) if raw else None
        except ValueError:
            amt = None
        out.append({
            "invoice_no": g(r, "RECHNUNGSNUMMER"),
            "kind": g(r, "ART") or "Rechnung",
            "customer_account": g(r, "DEBITOR"),
            "customer_name": g(r, "DEBITORNAME"),
            "invoice_date": g(r, "FAKTURADATUM"),
            "service_date": g(r, "LEISTUNGSDATUM"),
            "amount": amt, "amount_raw": raw,
            "amount_dec": (repr(amt) if amt is not None else None),
            "currency": g(r, "WAEHRUNG") or "EUR",
            "note": g(r, "BEMERKUNG"),
            "goods_issue_ref": g(r, "WARENAUSGANG_NR"),
            "storno_ref": g(r, "STORNO_REF"),
            "source_id": sid,
        })
    return out


def bridge(tables: Dict[str, list], data_dir=None, registry=None) -> List[str]:
    """Mutate `tables` in place: fill empty canonical tables from ext_ tables
    (and, if provided, from a directly-parsed extended invoice journal).
    Returns a list of human-readable bridge actions taken."""
    log: List[str] = []

    # 0) extended invoice journal parsed straight from disk (authoritative
    #    in-year revenue source, with goods-issue linkage).
    if data_dir is not None and registry is not None:
        ext = parse_extended_invoice_journal(data_dir, registry)
        if ext and len(ext) > len(tables.get("sales_invoices", [])):
            tables["sales_invoices"] = ext
            log.append(f"sales_invoices <- extended journal on disk ({len(ext)} rows)")

    def empty(name):
        return not tables.get(name)

    # 1) invoice journal -> sales_invoices. Prefer an EXTENDED journal (has a
    #    goods-issue column) if the auto-adapter produced one; else any ext
    #    invoice journal. Only bridge when sales_invoices is empty or clearly
    #    smaller than the ext source (finals: builtin caught only the Jan-2026
    #    file, missing the 21k-row 2025 extended journal).
    ext_inv = None
    for cand in ("ext_invoice_journal_erweitert", "ext_fakturajournal_2025_erweitert",
                 "ext_invoice_journal", "ext_sales_journal"):
        if tables.get(cand):
            if ext_inv is None or len(tables[cand]) > len(tables.get(ext_inv, [])):
                ext_inv = cand
    # also scan any ext_ table that looks like an invoice journal
    for name, rows in tables.items():
        if name.startswith("ext_") and rows and any(
                k in rows[0] for k in ("invoice_number", "RECHNUNGSNUMMER")):
            if ext_inv is None or len(rows) > len(tables.get(ext_inv, [])):
                ext_inv = name
    if ext_inv and len(tables.get(ext_inv, [])) > len(tables.get("sales_invoices", [])):
        mapped = [_map_invoice_journal(r) for r in tables[ext_inv]]
        tables["sales_invoices"] = mapped
        log.append(f"sales_invoices <- {ext_inv} ({len(mapped)} rows)")

    # 2) trial_balance
    if empty("trial_balance") and tables.get("ext_trial_balance"):
        tables["trial_balance"] = [_map_trial_balance(r) for r in tables["ext_trial_balance"]]
        log.append(f"trial_balance <- ext_trial_balance ({len(tables['trial_balance'])} rows)")

    # 3) op accounts
    if empty("op_creditors_accounts") and tables.get("ext_vendor_open_items"):
        tables["op_creditors_accounts"] = _map_vendor_open(tables["ext_vendor_open_items"])
        log.append(f"op_creditors_accounts <- ext_vendor_open_items "
                   f"({len(tables['op_creditors_accounts'])} rows)")
    if empty("op_debitors_accounts"):
        for src in ("ext_customer_open_items", "ext_debtor_open_items"):
            if tables.get(src):
                tables["op_debitors_accounts"] = _map_debtor_open(tables[src])
                log.append(f"op_debitors_accounts <- {src} "
                           f"({len(tables['op_debitors_accounts'])} rows)")
                break

    # 4) masterdata_changes (append ext customer master changes)
    if tables.get("ext_customer_master_changes"):
        add = [_map_masterdata(r) for r in tables["ext_customer_master_changes"]]
        tables.setdefault("masterdata_changes", [])
        # avoid double-bridge on re-run: only add if not already present
        have = {(m.get("account"), m.get("change_date"), m.get("field"))
                for m in tables["masterdata_changes"]}
        new = [m for m in add if (m.get("account"), m.get("change_date"),
                                  m.get("field")) not in have]
        if new:
            tables["masterdata_changes"].extend(new)
            log.append(f"masterdata_changes += ext_customer_master_changes ({len(new)} rows)")

    return log
