"""Deterministic paired control-scheme mutations for the practice dossier.

This generator creates three fraud/repair pairs without changing the source
dossier.  Financial totals are kept unchanged: two pairs only alter control
evidence, while threshold splitting replaces one payment with four component
payments whose sum is exactly the original amount.

Usage:
    python3 -m evalx.mutations_controls \
        --data data/practice --out-root data
    python3 -m evalx.mutations_controls --out-root data --verify-only
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from evalx.mutation_suite import dossier_hash


ENC = "cp1252"
GL_REL = "Sachkonten/Sachkontobuchungen.txt"
VT_REL = "Kreditoren/Lieferantenbuchungen.txt"
VENDOR_REL = "Kreditoren/Lieferanten.txt"
RECEIPT_REL = "Begleitdokumente/Wareneingangsliste_2025.csv"
CHANGE_REL = "Begleitdokumente/Stammdatenaenderungen_2025.csv"

SCHEMES = (
    "fictitious_vendor",
    "threshold_splitting",
    "bank_change_then_payment",
)
VARIANTS = ("fraud", "repair")

FV_VENDOR = "209112"
FV_NAME = "Vega Werkstoffe GmbH"
FV_CREATOR = "MV-U03"
FV_APPROVER = "MV-U02"
FV_INVOICES = ("ER901442", "ER901443", "ER901444", "ER901445")

TS_VENDOR = "200100"
TS_INVOICE = "ER900149"
TS_ORIGINAL_ENTRY = "7701170"
TS_ORIGINAL_PAYMENT = Decimal("39843.58")
TS_AMOUNTS = (
    Decimal("9960.00"),
    Decimal("9960.00"),
    Decimal("9960.00"),
    Decimal("9963.58"),
)
TS_ENTRY_IDS = ("7799101", "7799102", "7799103", "7799104")
TS_PAYMENT_REFS = ("AZSYN0253-1", "AZSYN0253-2", "AZSYN0253-3", "AZSYN0253-4")
TS_DATE = "19.02.2025"
TS_BATCH_REL = "Begleitdokumente/Batchfreigaben_Synthetisch.csv"

BC_VENDOR = "200075"
BC_NAME = "Delta Logistik SE"
BC_INVOICE = "ER900996"
BC_ENTRY = "7706862"
BC_PAYMENT_REF = "AZ6602281"
BC_AMOUNT = Decimal("99851.71")
BC_CHANGE_DATE = "17.10.2025"
BC_PAYMENT_DATE = "20.10.2025"
BC_CHANGER = "MV-U03"
BC_APPROVER = "MV-U02"
BC_OLD_IBAN = "DE02100100100012345678"
BC_NEW_IBAN = "DE44500105175407324931"
BC_DETAIL_REL = "Begleitdokumente/Bankwechsel_Zahlungsdetails_Synthetisch.csv"


def _parse_amount(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


def _format_amount(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",")


def _read_rows(path: Path, encoding: str = ENC) -> list[list[str]]:
    with path.open("r", encoding=encoding, newline="") as handle:
        return list(csv.reader(handle, delimiter=";"))


def _line_parts(path: Path) -> tuple[list[str], str, bool]:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    trailing = raw.endswith((b"\r\n", b"\n"))
    return raw.decode(ENC).splitlines(), newline, trailing


def _write_lines(path: Path, lines: list[str], newline: str, trailing: bool = True) -> None:
    text = newline.join(lines) + (newline if trailing else "")
    path.write_bytes(text.encode(ENC))


def _find_lines(path: Path, predicate) -> list[tuple[int, list[str], str]]:
    lines, _, _ = _line_parts(path)
    hits: list[tuple[int, list[str], str]] = []
    for lineno, line in enumerate(lines, start=1):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            hits.append((lineno, row, line))
    return hits


def _replace_unique_line(path: Path, predicate, replacement: str) -> dict[str, Any]:
    lines, newline, trailing = _line_parts(path)
    indexes = []
    for index, line in enumerate(lines):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            indexes.append(index)
    if len(indexes) != 1:
        raise RuntimeError(f"expected one matching line in {path}, got {len(indexes)}")
    index = indexes[0]
    original = lines[index]
    lines[index] = replacement
    _write_lines(path, lines, newline, trailing)
    return {"line": index + 1, "before": original, "after": replacement}


def _replace_unique_with_many(path: Path, predicate, replacements: list[str]) -> dict[str, Any]:
    lines, newline, trailing = _line_parts(path)
    indexes = []
    for index, line in enumerate(lines):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            indexes.append(index)
    if len(indexes) != 1:
        raise RuntimeError(f"expected one matching line in {path}, got {len(indexes)}")
    index = indexes[0]
    original = lines[index]
    lines[index:index + 1] = replacements
    _write_lines(path, lines, newline, trailing)
    return {
        "source_line": index + 1,
        "before": original,
        "generated_lines": list(range(index + 1, index + 1 + len(replacements))),
        "after": replacements,
    }


def _delete_lines(path: Path, predicate) -> list[dict[str, Any]]:
    lines, newline, trailing = _line_parts(path)
    deleted: list[dict[str, Any]] = []
    kept = []
    for lineno, line in enumerate(lines, start=1):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            deleted.append({"line": lineno, "content": line})
        else:
            kept.append(line)
    _write_lines(path, kept, newline, trailing)
    return deleted


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store"):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _copy_base(data_dir: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(data_dir, target, ignore=shutil.ignore_patterns(".DS_Store"))


def _change_row(date: str, account: str, name: str, field: str, old: str,
                new: str, changed_by: str, approved_by: str) -> str:
    return ";".join((date, "Kreditor", account, name, field, old, new,
                     changed_by, approved_by, "Ja"))


def _gl_payment_line(account: str, credit: bool, amount: Decimal,
                     payment_ref: str, entry_id: str, time: str) -> str:
    signed = -amount if credit else amount
    return (
        f'"{account}";"Periode 2";"";"Vortrag";"Zahlung";"Nein";'
        f'"{"Ja" if credit else "Nein"}";{_format_amount(signed)};"EUR";'
        f'{_format_amount(signed)};"Zahlungsausgang";{TS_DATE};'
        f'"{payment_ref}";{TS_DATE};"{TS_INVOICE}";"Aktuell";"";"";'
        f'{entry_id};"{TS_INVOICE}";"MV-U11";{TS_DATE};"{time}";"Ja"'
    )


def _vt_payment_line(amount: Decimal) -> str:
    return (
        f'"{TS_VENDOR}";"{TS_INVOICE}";{TS_DATE};"";{TS_DATE};'
        f'"Zahlungsausgang";{_format_amount(amount)};"EUR";'
        f'{_format_amount(amount)};"";"";"Purch";"Yes"'
    )


def _base_metadata(data_dir: Path, rels: list[str]) -> dict[str, Any]:
    return {
        "tree_sha256": _tree_hash(data_dir),
        "base_dossier_hash": dossier_hash(data_dir),
        "affected_file_sha256": {
            rel: _sha256(data_dir / rel) for rel in sorted(set(rels))
        },
    }


def _build_fictitious_vendor(data_dir: Path, out_root: Path,
                             base_meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for variant in VARIANTS:
        target = out_root / "mutated_fictitious_vendor" / variant
        _copy_base(data_dir, target)

        approved_by = FV_CREATOR if variant == "fraud" else FV_APPROVER
        replacement = _change_row(
            "03.06.2025", FV_VENDOR, FV_NAME, "Neuanlage Kreditor", "",
            "angelegt inkl. Bankverbindung", FV_CREATOR, approved_by,
        )
        change = _replace_unique_line(
            target / CHANGE_REL,
            lambda r: len(r) >= 10 and r[2] == FV_VENDOR
            and r[4] == "Neuanlage Kreditor",
            replacement,
        )
        deleted = []
        if variant == "fraud":
            deleted = _delete_lines(
                target / RECEIPT_REL,
                lambda r: len(r) >= 4 and r[3] == FV_VENDOR,
            )

        result[variant] = {
            "mutation": "fictitious_vendor_control_bypass",
            "variant": variant,
            "source_base": base_meta,
            "ground_truth": {
                "vendor": FV_VENDOR,
                "vendor_name": FV_NAME,
                "invoice_references": list(FV_INVOICES),
                "creator": FV_CREATOR,
                "approver": approved_by,
                "self_approved": variant == "fraud",
                "goods_receipts_expected": 0 if variant == "fraud" else 4,
                "label": "fraud" if variant == "fraud" else "innocent_repair",
            },
            "affected_files_and_rows": {
                CHANGE_REL: [change],
                RECEIPT_REL: deleted if deleted else [
                    {"line": n, "content": raw} for n, _, raw in _find_lines(
                        target / RECEIPT_REL,
                        lambda r: len(r) >= 4 and r[3] == FV_VENDOR,
                    )
                ],
            },
            "expected_detection": (
                "new vendor is self-created/self-approved and all four posted "
                "invoice references lack goods-receipt support"
                if variant == "fraud" else
                "same vendor/invoices are supported by four goods receipts and "
                "independent master-data approval; defender should reject fraud"
            ),
            "innocent_predicate": (
                "independent vendor-master approval exists AND every material "
                "invoice resolves to a matching goods receipt"
            ),
        }
    return result


def _build_threshold_splitting(data_dir: Path, out_root: Path,
                               base_meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    times = ("17:17:12", "17:18:03", "17:18:51", "17:19:27")
    gl_replacements: list[str] = []
    vt_replacements: list[str] = []
    for amount, entry_id, payment_ref, time in zip(
            TS_AMOUNTS, TS_ENTRY_IDS, TS_PAYMENT_REFS, times):
        gl_replacements.extend((
            _gl_payment_line(f"330000-{TS_VENDOR}", False, amount,
                             payment_ref, entry_id, time),
            _gl_payment_line("271000", True, amount,
                             payment_ref, entry_id, time),
        ))
        vt_replacements.append(_vt_payment_line(amount))

    for variant in VARIANTS:
        target = out_root / "mutated_threshold_splitting" / variant
        _copy_base(data_dir, target)
        gl_change = _replace_unique_with_many(
            target / GL_REL,
            lambda r: len(r) >= 19 and r[18] == TS_ORIGINAL_ENTRY
            and r[0] == f"330000-{TS_VENDOR}",
            gl_replacements[0:2],
        )
        # Replace the paired bank row at its shifted position with the six
        # remaining rows, yielding four balanced entry groups in total.
        bank_change = _replace_unique_with_many(
            target / GL_REL,
            lambda r: len(r) >= 19 and r[18] == TS_ORIGINAL_ENTRY
            and r[0] == "271000",
            gl_replacements[2:],
        )
        # The first replacement inserted one extra line before the original
        # bank line.  Ground truth reports the location in the source dossier,
        # while generated_lines continue to report locations in the variant.
        bank_change["source_line"] -= 1
        vt_change = _replace_unique_with_many(
            target / VT_REL,
            lambda r: len(r) >= 6 and r[0] == TS_VENDOR
            and r[1] == TS_INVOICE and r[5] == "Zahlungsausgang",
            vt_replacements,
        )
        batch_status = "Nicht dokumentiert" if variant == "fraud" else "Freigegeben"
        approver = "" if variant == "fraud" else "MV-U02"
        batch_lines = [
            "BATCH_ID;DATUM;KREDITOR;RECHNUNG;TEILZAHLUNGEN;GESAMT_EUR;"
            "ERSTELLER;FREIGEBER;STATUS",
            f"BATCH-SYN-0253;{TS_DATE};{TS_VENDOR};{TS_INVOICE};4;"
            f"{_format_amount(TS_ORIGINAL_PAYMENT)};MV-U11;{approver};{batch_status}",
        ]
        _write_lines(target / TS_BATCH_REL, batch_lines, "\r\n", True)
        result[variant] = {
            "mutation": "threshold_splitting",
            "variant": variant,
            "source_base": base_meta,
            "ground_truth": {
                "vendor": TS_VENDOR,
                "invoice": TS_INVOICE,
                "original_entry_id": TS_ORIGINAL_ENTRY,
                "original_amount_eur": str(TS_ORIGINAL_PAYMENT),
                "split_amounts_eur": [str(v) for v in TS_AMOUNTS],
                "split_entry_ids": list(TS_ENTRY_IDS),
                "split_payment_references": list(TS_PAYMENT_REFS),
                "individual_threshold_eur": "10000.00",
                "aggregate_authorized": variant == "repair",
                "label": "fraud" if variant == "fraud" else "innocent_repair",
            },
            "affected_files_and_rows": {
                GL_REL: [gl_change, bank_change],
                VT_REL: [vt_change],
                TS_BATCH_REL: [{"line": 2, "content": batch_lines[1]}],
            },
            "expected_detection": (
                "four same-day sub-threshold payments settle one invoice without "
                "documented aggregate authorization"
                if variant == "fraud" else
                "identical four payments resolve to one independently approved "
                "batch for the full invoice; defender should reject evasion"
            ),
            "innocent_predicate": (
                "all components resolve to one pre-approved batch whose aggregate "
                "equals the original invoice payment"
            ),
        }
    return result


def _build_bank_change(data_dir: Path, out_root: Path,
                       base_meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    details = [
        "WECHSEL_ID;AENDERUNGSDATUM;KREDITOR;ALTE_IBAN;NEUE_IBAN;"
        "ZAHLUNGSDATUM;ZAHLUNGSREFERENZ;RECHNUNG;ENTRY_ID;BETRAG_EUR;VERWENDETE_IBAN",
        f"BC-SYN-200075;{BC_CHANGE_DATE};{BC_VENDOR};{BC_OLD_IBAN};{BC_NEW_IBAN};"
        f"{BC_PAYMENT_DATE};{BC_PAYMENT_REF};{BC_INVOICE};{BC_ENTRY};"
        f"{_format_amount(BC_AMOUNT)};{BC_NEW_IBAN}",
    ]
    for variant in VARIANTS:
        target = out_root / "mutated_bank_change_then_payment" / variant
        _copy_base(data_dir, target)
        approved_by = BC_CHANGER if variant == "fraud" else BC_APPROVER
        replacement = _change_row(
            BC_CHANGE_DATE, BC_VENDOR, BC_NAME, "Bankverbindung",
            BC_OLD_IBAN, BC_NEW_IBAN, BC_CHANGER, approved_by,
        )
        change = _replace_unique_line(
            target / CHANGE_REL,
            lambda r: len(r) >= 10 and r[2] == BC_VENDOR
            and r[4] == "Bankverbindung",
            replacement,
        )
        _write_lines(target / BC_DETAIL_REL, details, "\r\n", True)
        result[variant] = {
            "mutation": "bank_change_then_payment",
            "variant": variant,
            "source_base": base_meta,
            "ground_truth": {
                "vendor": BC_VENDOR,
                "vendor_name": BC_NAME,
                "bank_change_date": BC_CHANGE_DATE,
                "old_iban": BC_OLD_IBAN,
                "new_iban": BC_NEW_IBAN,
                "changed_by": BC_CHANGER,
                "approved_by": approved_by,
                "self_approved": variant == "fraud",
                "payment_date": BC_PAYMENT_DATE,
                "payment_reference": BC_PAYMENT_REF,
                "invoice": BC_INVOICE,
                "entry_id": BC_ENTRY,
                "amount_eur": str(BC_AMOUNT),
                "used_iban": BC_NEW_IBAN,
                "days_change_to_payment": 3,
                "label": "fraud" if variant == "fraud" else "innocent_repair",
            },
            "affected_files_and_rows": {
                CHANGE_REL: [change],
                BC_DETAIL_REL: [{"line": 2, "content": details[1]}],
            },
            "expected_detection": (
                "payment to a newly changed bank account three days after a "
                "self-approved master-data change"
                if variant == "fraud" else
                "same bank change and payment have independent approval; defender "
                "should reject control-bypass allegation"
            ),
            "innocent_predicate": (
                "bank-account change was independently approved before the payment"
            ),
        }
    return result


def _base_financial_state(root: Path) -> dict[str, Any]:
    gl = _read_rows(root / GL_REL)
    vt = _read_rows(root / VT_REL)
    return {
        "gl_rows": len(gl),
        "vendor_rows": len(vt),
        "gl_total": sum((_parse_amount(r[7]) for r in gl), Decimal("0")),
        "vendor_total": sum((_parse_amount(r[6]) for r in vt), Decimal("0")),
        "receipt_rows": len(_read_rows(root / RECEIPT_REL)) - 1,
        "change_rows": len(_read_rows(root / CHANGE_REL)) - 1,
    }


def verify_variant(root: Path, scheme: str, variant: str,
                   base_state: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    gl = _read_rows(root / GL_REL)
    vt = _read_rows(root / VT_REL)
    total = sum((_parse_amount(r[7]) for r in gl), Decimal("0"))
    check("gl_total_unchanged", total == base_state["gl_total"],
          f"variant={total}, base={base_state['gl_total']}")

    grouped: dict[str, Decimal] = {}
    for row in gl:
        grouped[row[18]] = grouped.get(row[18], Decimal("0")) + _parse_amount(row[7])
    unbalanced = {key: str(value) for key, value in grouped.items() if value != 0}
    check("every_gl_entry_balanced", not unbalanced,
          f"entry_groups={len(grouped)}, unbalanced={unbalanced or 'none'}")

    gl_vendor = sum((_parse_amount(r[7]) for r in gl if r[0].startswith("330000-")), Decimal("0"))
    vt_total = sum((_parse_amount(r[6]) for r in vt), Decimal("0"))
    check("vendor_subledger_ties_gl", gl_vendor == vt_total,
          f"gl_vendor={gl_vendor}, vendor_subledger={vt_total}")

    expected_gl_delta = 6 if scheme == "threshold_splitting" else 0
    expected_vt_delta = 3 if scheme == "threshold_splitting" else 0
    check("gl_row_delta", len(gl) - base_state["gl_rows"] == expected_gl_delta,
          f"delta={len(gl) - base_state['gl_rows']}, expected={expected_gl_delta}")
    check("vendor_row_delta", len(vt) - base_state["vendor_rows"] == expected_vt_delta,
          f"delta={len(vt) - base_state['vendor_rows']}, expected={expected_vt_delta}")

    if scheme == "fictitious_vendor":
        receipts = _read_rows(root / RECEIPT_REL)
        matches = [r for r in receipts[1:] if len(r) >= 4 and r[3] == FV_VENDOR]
        expected = 0 if variant == "fraud" else 4
        check("target_receipt_count", len(matches) == expected,
              f"count={len(matches)}, expected={expected}")
        receipt_delta = (len(receipts) - 1) - base_state["receipt_rows"]
        expected_delta = -4 if variant == "fraud" else 0
        check("receipt_row_delta", receipt_delta == expected_delta,
              f"delta={receipt_delta}, expected={expected_delta}")
        gl_invoices = {r[14] for r in gl if r[0] == f"330000-{FV_VENDOR}"}
        vt_invoices = {r[1] for r in vt if r[0] == FV_VENDOR and _parse_amount(r[6]) < 0}
        check("invoice_references_resolve", set(FV_INVOICES) <= gl_invoices & vt_invoices,
              f"gl={sorted(gl_invoices)}, vendor={sorted(vt_invoices)}")
        changes = [r for r in _read_rows(root / CHANGE_REL)[1:]
                   if len(r) >= 10 and r[2] == FV_VENDOR and r[4] == "Neuanlage Kreditor"]
        expected_approver = FV_CREATOR if variant == "fraud" else FV_APPROVER
        check("approval_state_exact", len(changes) == 1 and changes[0][8] == expected_approver,
              f"rows={len(changes)}, expected_approver={expected_approver}")
        dates_ok = all(datetime.strptime(r[1], "%d.%m.%Y").year == 2025 for r in matches)
        check("target_dates_in_fy2025", dates_ok, f"receipt_count={len(matches)}")

    elif scheme == "threshold_splitting":
        split_rows = [r for r in gl if r[18] in TS_ENTRY_IDS]
        check("four_balanced_split_entries", len(split_rows) == 8 and all(
            sum((_parse_amount(r[7]) for r in split_rows if r[18] == eid), Decimal("0")) == 0
            for eid in TS_ENTRY_IDS), f"rows={len(split_rows)}")
        debits = [_parse_amount(r[7]) for r in split_rows if r[0] == f"330000-{TS_VENDOR}"]
        check("split_sum_equals_original", sum(debits, Decimal("0")) == TS_ORIGINAL_PAYMENT,
              f"sum={sum(debits, Decimal('0'))}, original={TS_ORIGINAL_PAYMENT}")
        check("all_components_below_threshold", len(debits) == 4 and all(v < 10000 for v in debits),
              f"amounts={[str(v) for v in debits]}")
        check("original_payment_replaced", not any(r[18] == TS_ORIGINAL_ENTRY for r in gl),
              f"original_entry={TS_ORIGINAL_ENTRY}")
        invoice_posting = [r for r in gl if r[18] == "7701003" and r[14] == TS_INVOICE]
        check("invoice_reference_resolves", len(invoice_posting) == 3,
              f"invoice_posting_rows={len(invoice_posting)}")
        batch = _read_rows(root / TS_BATCH_REL)
        expected_status = "Nicht dokumentiert" if variant == "fraud" else "Freigegeben"
        check("batch_control_state_exact", len(batch) == 2 and batch[1][8] == expected_status,
              f"status={batch[1][8] if len(batch) > 1 else 'missing'}")
        check("split_dates_in_fy2025", all(
            datetime.strptime(r[11], "%d.%m.%Y").year == 2025 for r in split_rows),
            f"rows={len(split_rows)}")

    elif scheme == "bank_change_then_payment":
        details = _read_rows(root / BC_DETAIL_REL)
        check("payment_detail_row_exact", len(details) == 2 and details[1][2] == BC_VENDOR
              and details[1][8] == BC_ENTRY and _parse_amount(details[1][9]) == BC_AMOUNT,
              f"rows={len(details) - 1}")
        gl_payment = [r for r in gl if r[18] == BC_ENTRY and r[14] == BC_INVOICE]
        vt_payment = [r for r in vt if r[0] == BC_VENDOR and r[1] == BC_INVOICE
                      and r[5] == "Zahlungsausgang"]
        check("payment_reference_resolves", len(gl_payment) == 2 and len(vt_payment) == 1,
              f"gl_rows={len(gl_payment)}, vendor_rows={len(vt_payment)}")
        changes = [r for r in _read_rows(root / CHANGE_REL)[1:]
                   if len(r) >= 10 and r[2] == BC_VENDOR and r[4] == "Bankverbindung"]
        expected_approver = BC_CHANGER if variant == "fraud" else BC_APPROVER
        check("approval_state_exact", len(changes) == 1 and changes[0][8] == expected_approver,
              f"rows={len(changes)}, expected_approver={expected_approver}")
        change_date = datetime.strptime(BC_CHANGE_DATE, "%d.%m.%Y")
        payment_date = datetime.strptime(BC_PAYMENT_DATE, "%d.%m.%Y")
        check("change_precedes_payment_in_fy2025", change_date < payment_date
              and change_date.year == payment_date.year == 2025,
              f"change={BC_CHANGE_DATE}, payment={BC_PAYMENT_DATE}")

    return checks


def _verify_pair(out_root: Path, scheme: str, base_state: dict[str, Any]) -> dict[str, Any]:
    pair = {}
    for variant in VARIANTS:
        root = out_root / f"mutated_{scheme}" / variant
        pair[variant] = verify_variant(root, scheme, variant, base_state)

    financial_rels = (GL_REL, VT_REL, VENDOR_REL)
    same = all(
        _sha256(out_root / f"mutated_{scheme}" / "fraud" / rel)
        == _sha256(out_root / f"mutated_{scheme}" / "repair" / rel)
        for rel in financial_rels
    )
    pair["symmetry"] = [{
        "name": "fraud_repair_financial_files_identical",
        "passed": same,
        "detail": ", ".join(financial_rels),
    }]
    return pair


def _write_manifest(root: Path, payload: dict[str, Any], checks: list[dict[str, Any]],
                    symmetry: list[dict[str, Any]]) -> None:
    manifest = {
        "schema_version": "1.0",
        "generator": "evalx.mutations_controls",
        "deterministic": True,
        "llm_used": False,
        **payload,
        "base_dossier_hash": payload["source_base"]["base_dossier_hash"],
        "affected_files": sorted(payload["affected_files_and_rows"]),
        "invariant_results": checks + symmetry,
        "all_invariants_passed": all(c["passed"] for c in checks + symmetry),
    }
    (root / "MUTATION_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def generate(data_dir: Path, out_root: Path) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    out_root = out_root.resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(data_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    base_state = _base_financial_state(data_dir)

    metas = {
        "fictitious_vendor": _base_metadata(data_dir, [CHANGE_REL, RECEIPT_REL, GL_REL, VT_REL]),
        "threshold_splitting": _base_metadata(data_dir, [GL_REL, VT_REL]),
        "bank_change_then_payment": _base_metadata(data_dir, [CHANGE_REL, GL_REL, VT_REL]),
    }
    payloads = {}
    payloads["fictitious_vendor"] = _build_fictitious_vendor(
        data_dir, out_root, metas["fictitious_vendor"])
    payloads["threshold_splitting"] = _build_threshold_splitting(
        data_dir, out_root, metas["threshold_splitting"])
    payloads["bank_change_then_payment"] = _build_bank_change(
        data_dir, out_root, metas["bank_change_then_payment"])

    report = {}
    for scheme in SCHEMES:
        pair = _verify_pair(out_root, scheme, base_state)
        for variant in VARIANTS:
            root = out_root / f"mutated_{scheme}" / variant
            _write_manifest(root, payloads[scheme][variant], pair[variant], pair["symmetry"])
        report[scheme] = pair
    return report


def verify_existing(data_dir: Path, out_root: Path) -> dict[str, Any]:
    base_state = _base_financial_state(data_dir.resolve())
    report = {}
    for scheme in SCHEMES:
        for variant in VARIANTS:
            root = out_root.resolve() / f"mutated_{scheme}" / variant
            if not root.is_dir():
                raise FileNotFoundError(root)
        report[scheme] = _verify_pair(out_root.resolve(), scheme, base_state)
    return report


def _summarize(report: dict[str, Any]) -> tuple[bool, str]:
    lines = []
    passed_all = True
    for scheme in SCHEMES:
        checks = report[scheme]
        scheme_checks = checks["fraud"] + checks["repair"] + checks["symmetry"]
        passed = sum(1 for item in scheme_checks if item["passed"])
        total = len(scheme_checks)
        ok = passed == total
        passed_all = passed_all and ok
        lines.append(f"{scheme}: {passed}/{total} checks passed")
        for item in scheme_checks:
            if not item["passed"]:
                lines.append(f"  FAIL {item['name']}: {item['detail']}")
    return passed_all, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/practice"))
    parser.add_argument("--out-root", type=Path, default=Path("data"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    report = (verify_existing(args.data, args.out_root) if args.verify_only
              else generate(args.data, args.out_root))
    passed, summary = _summarize(report)
    print(summary)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
