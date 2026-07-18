"""Generate deterministic paired reporting-scheme mutation dossiers.

Pairs:
* fictitious_sales: an existing posted invoice loses/retains goods issue proof;
* cutoff: a December obligation is excluded from/included in an existing accrual;
* repair_capitalization: a repair-named asset lacks/has a technical assessment.

Financial records are copied byte-for-byte from the practice dossier.  Only
supporting evidence changes, so the fraud and repair twins have identical GL,
subledger, trial-balance, and report totals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from evalx.mutation_suite import dossier_hash, verify_gl


ENC = "cp1252"
GL = "Sachkonten/Sachkontobuchungen.txt"
CUSTOMER_TX = "Debitoren/Kundenbuchungen.txt"
VENDOR_TX = "Kreditoren/Lieferantenbuchungen.txt"
ASSET_MASTER = "AV/Anlagen.txt"
ASSET_TX = "AV/Anlagenbuchungen.txt"
SALES_JOURNAL = "Begleitdokumente/Fakturajournal_2025.csv"
GOODS_ISSUE = "Begleitdokumente/Warenausgangsliste_2025.csv"
SUBSEQUENT_INVOICES = "Begleitdokumente/Fakturajournal_Januar_2026_Kreditoren.csv"
CUTOFF_EVIDENCE = "Begleitdokumente/Abgrenzungsnachweis_Synthetisch.csv"
TECH_EVIDENCE = "Begleitdokumente/Technische_Beurteilungen_Synthetisch.csv"

SCHEMES = ("fictitious_sales", "cutoff", "repair_capitalization")
VARIANTS = ("fraud", "repair")
CORE_FINANCIAL = (GL, CUSTOMER_TX, VENDOR_TX, ASSET_MASTER, ASSET_TX)

SALE_INVOICE = "AR500000"
SALE_CUSTOMER = "100145"
SALE_GOODS_ISSUE = "WA300000"
SALE_NET = Decimal("18857.00")
SALE_GROSS = Decimal("22439.83")

CUTOFF_INVOICE = "ER901427"
CUTOFF_VENDOR = "209130"
CUTOFF_SERVICE_DATE = "21.12.2025"
CUTOFF_INVOICE_DATE = "15.01.2026"
CUTOFF_AMOUNT = Decimal("22000.00")
CUTOFF_ACCRUAL_REF = "GJ6602864"
CUTOFF_ACCRUAL_ENTRY = "7708372"
CUTOFF_ACCRUAL_AMOUNT = Decimal("86500.00")

CAP_ASSET = "040000-000191"
CAP_INVOICE = "ER901421"
CAP_VENDOR = "200056"
CAP_NET = Decimal("28000.00")
CAP_GROSS = Decimal("33320.00")
CAP_POSTING_DATE = "20.11.2025"


def _amount(raw: str) -> Decimal:
    return Decimal(raw.replace(".", "").replace(",", "."))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding=ENC, newline="") as fh:
        return list(csv.reader(fh, delimiter=";"))


def _raw_lines(path: Path) -> tuple[list[str], str, bool]:
    raw = path.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode(ENC).splitlines(), nl, raw.endswith((b"\n", b"\r\n"))


def _write_lines(path: Path, lines: list[str], nl: str = "\r\n",
                 trailing: bool = True) -> None:
    path.write_bytes((nl.join(lines) + (nl if trailing else "")).encode(ENC))


def _matching_rows(path: Path, predicate) -> list[dict[str, Any]]:
    lines, _, _ = _raw_lines(path)
    found = []
    for lineno, line in enumerate(lines, 1):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            found.append({"line": lineno, "content": line})
    return found


def _delete_matching(path: Path, predicate) -> list[dict[str, Any]]:
    lines, nl, trailing = _raw_lines(path)
    deleted, kept = [], []
    for lineno, line in enumerate(lines, 1):
        row = next(csv.reader([line], delimiter=";"))
        if predicate(row):
            deleted.append({"line": lineno, "content": line})
        else:
            kept.append(line)
    _write_lines(path, kept, nl, trailing)
    return deleted


def _copy(data: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(data, target, ignore=shutil.ignore_patterns(".DS_Store"))


def _common_manifest(scheme: str, variant: str, base_hash: str,
                     affected: list[dict[str, Any]], truth: dict[str, Any],
                     expected: str, innocent: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generator": "evalx.mutations_reporting",
        "deterministic": True,
        "llm_used": False,
        "mutation": scheme,
        "variant": variant,
        "base_dossier_hash": base_hash,
        "affected_files": affected,
        "ground_truth": truth,
        "expected_detection": expected,
        "innocent_predicate": innocent,
    }


def _build_sales(data: Path, out: Path, base_hash: str) -> dict[str, dict[str, Any]]:
    manifests = {}
    for variant in VARIANTS:
        root = out / "mutated_fictitious_sales" / variant
        _copy(data, root)
        issue_path = root / GOODS_ISSUE
        if variant == "fraud":
            evidence = _delete_matching(
                issue_path,
                lambda r: len(r) >= 3 and r[2] == SALE_INVOICE,
            )
            operation = "deleted"
        else:
            evidence = _matching_rows(
                issue_path,
                lambda r: len(r) >= 3 and r[2] == SALE_INVOICE,
            )
            operation = "retained_as_exculpatory_evidence"
        manifests[variant] = _common_manifest(
            "fictitious_sales", variant, base_hash,
            [{"path": GOODS_ISSUE, "operation": operation, "rows": evidence}],
            {
                "label": "fraud" if variant == "fraud" else "innocent_repair",
                "invoice": SALE_INVOICE,
                "customer": SALE_CUSTOMER,
                "goods_issue": SALE_GOODS_ISSUE,
                "invoice_net_eur": str(SALE_NET),
                "invoice_gross_eur": str(SALE_GROSS),
                "goods_issue_count": 0 if variant == "fraud" else 1,
            },
            ("posted and later-paid sales invoice has no goods-issue record"
             if variant == "fraud" else
             "the same invoice resolves to its matching goods issue; defender should reject fictitious sale"),
            "invoice, customer, amount, and date resolve to a matching goods-issue record",
        )
    return manifests


def _cutoff_lines(included: bool) -> list[str]:
    target_status = "Enthalten" if included else "Nicht enthalten"
    target_alloc = Decimal("22000.00") if included else Decimal("0.00")
    remainder = CUTOFF_ACCRUAL_AMOUNT - target_alloc
    return [
        "NACHWEIS_ID;ABSCHLUSSDATUM;ACCRUAL_ENTRY_ID;ACCRUAL_REFERENZ;"
        "RECHNUNG;KREDITOR;LEISTUNGSDATUM;RECHNUNGSDATUM;VERPFLICHTUNG_EUR;"
        "ZUGEORDNET_EUR;STATUS;BESCHREIBUNG",
        f"ABG-SYN-TARGET;31.12.2025;{CUTOFF_ACCRUAL_ENTRY};{CUTOFF_ACCRUAL_REF};"
        f"{CUTOFF_INVOICE};{CUTOFF_VENDOR};{CUTOFF_SERVICE_DATE};{CUTOFF_INVOICE_DATE};"
        f"22000,00;{('22000,00' if included else '0,00')};{target_status};Frachten Dez 2025",
        f"ABG-SYN-OTHER;31.12.2025;{CUTOFF_ACCRUAL_ENTRY};{CUTOFF_ACCRUAL_REF};;;;;"
        f"{str(remainder).replace('.', ',')};{str(remainder).replace('.', ',')};Enthalten;"
        "Uebrige unfakturierte Leistungen laut Abschlussaufstellung",
    ]


def _build_cutoff(data: Path, out: Path, base_hash: str) -> dict[str, dict[str, Any]]:
    manifests = {}
    for variant in VARIANTS:
        root = out / "mutated_cutoff" / variant
        _copy(data, root)
        included = variant == "repair"
        lines = _cutoff_lines(included)
        _write_lines(root / CUTOFF_EVIDENCE, lines)
        manifests[variant] = _common_manifest(
            "cutoff", variant, base_hash,
            [{
                "path": CUTOFF_EVIDENCE,
                "operation": "synthetic_accrual_rollforward",
                "rows": [
                    {"line": 2, "content": lines[1]},
                    {"line": 3, "content": lines[2]},
                ],
            }],
            {
                "label": "fraud" if variant == "fraud" else "innocent_repair",
                "subsequent_invoice": CUTOFF_INVOICE,
                "vendor": CUTOFF_VENDOR,
                "service_date": CUTOFF_SERVICE_DATE,
                "invoice_date": CUTOFF_INVOICE_DATE,
                "obligation_eur": str(CUTOFF_AMOUNT),
                "accrual_entry_id": CUTOFF_ACCRUAL_ENTRY,
                "accrual_reference": CUTOFF_ACCRUAL_REF,
                "accrual_total_eur": str(CUTOFF_ACCRUAL_AMOUNT),
                "allocated_to_invoice_eur": "22000.00" if included else "0.00",
                "included_in_accrual_schedule": included,
            },
            ("December-service subsequent invoice is explicitly excluded from the year-end accrual schedule"
             if variant == "fraud" else
             "the same obligation is allocated inside the existing balanced accrual; defender should reject cutoff gap"),
            "subsequent invoice with a 2025 service date is explicitly included in a cited year-end accrual schedule",
        )
    return manifests


def _assessment_lines(present: bool) -> list[str]:
    header = (
        "BEURTEILUNG_ID;DATUM;ANLAGE;RECHNUNG;BEZEICHNUNG;TECHNISCHER_BEURTEILER;"
        "BEFUND;KAPAZITAET_VORHER;KAPAZITAET_NACHHER;NUTZUNGSDAUER_PLUS_JAHRE;"
        "FREIGEBER;STATUS"
    )
    if not present:
        return [header]
    return [
        header,
        f"TB-SYN-0191;18.11.2025;{CAP_ASSET};{CAP_INVOICE};"
        "Reparatur Konfektioniermaschine Linie 2;ING-U04;"
        "Umbau erweitert Leistung und ersetzt zentrale Steuerung;100;118;4;MV-U10;Freigegeben",
    ]


def _build_capitalization(data: Path, out: Path,
                          base_hash: str) -> dict[str, dict[str, Any]]:
    manifests = {}
    for variant in VARIANTS:
        root = out / "mutated_repair_capitalization" / variant
        _copy(data, root)
        present = variant == "repair"
        lines = _assessment_lines(present)
        _write_lines(root / TECH_EVIDENCE, lines)
        rows = [] if not present else [{"line": 2, "content": lines[1]}]
        manifests[variant] = _common_manifest(
            "repair_capitalization", variant, base_hash,
            [{
                "path": TECH_EVIDENCE,
                "operation": "assessment_absent" if not present else "assessment_added",
                "rows": rows,
            }],
            {
                "label": "fraud" if variant == "fraud" else "innocent_repair",
                "asset_id": CAP_ASSET,
                "invoice": CAP_INVOICE,
                "vendor": CAP_VENDOR,
                "description": "Reparatur Konfektioniermaschine Linie 2",
                "capitalized_net_eur": str(CAP_NET),
                "invoice_gross_eur": str(CAP_GROSS),
                "posting_date": CAP_POSTING_DATE,
                "technical_assessment_present": present,
                "capacity_increase_percent": 18 if present else None,
                "useful_life_extension_years": 4 if present else None,
            },
            ("repair-named expenditure is capitalized without technical evidence of future economic benefit"
             if variant == "fraud" else
             "the same capitalization has approved evidence of capacity and useful-life extension; defender should reject misclassification"),
            "approved pre-posting technical assessment documents capacity increase or useful-life extension",
        )
    return manifests


def _common_checks(root: Path, data: Path, base_hash: str) -> list[dict[str, Any]]:
    checks = []

    def ck(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    gl = verify_gl(root)
    ck("gl_total_and_entries_balanced", gl["passed"],
       f"rows={gl['row_count']}, entries={gl['entry_count']}, total={gl['total']}")
    ck("gl_unchanged_from_base", _sha256(root / GL) == _sha256(data / GL), GL)
    same_financial = all(_sha256(root / rel) == _sha256(data / rel) for rel in CORE_FINANCIAL)
    ck("all_core_financial_files_unchanged", same_financial, ", ".join(CORE_FINANCIAL))
    manifest = json.loads((root / "MUTATION_MANIFEST.json").read_text(encoding="utf-8")) \
        if (root / "MUTATION_MANIFEST.json").exists() else None
    if manifest is not None:
        ck("manifest_base_hash", manifest.get("base_dossier_hash") == base_hash,
           f"declared={manifest.get('base_dossier_hash')}, expected={base_hash}")
    return checks


def verify_variant(root: Path, data: Path, scheme: str, variant: str,
                   base_hash: str) -> list[dict[str, Any]]:
    checks = _common_checks(root, data, base_hash)

    def ck(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    gl = _rows(root / GL)
    if scheme == "fictitious_sales":
        issues = [r for r in _rows(root / GOODS_ISSUE)[1:] if r[2] == SALE_INVOICE]
        expected = 0 if variant == "fraud" else 1
        ck("goods_issue_row_count", len(issues) == expected,
           f"count={len(issues)}, expected={expected}")
        base_n = len(_rows(data / GOODS_ISSUE))
        delta = len(_rows(root / GOODS_ISSUE)) - base_n
        ck("goods_issue_file_row_delta", delta == (-1 if variant == "fraud" else 0),
           f"delta={delta}")
        journal = [r for r in _rows(root / SALES_JOURNAL)[1:] if r[0] == SALE_INVOICE]
        customer = [r for r in _rows(root / CUSTOMER_TX) if r[1] == SALE_INVOICE]
        gl_rows = [r for r in gl if r[14] == SALE_INVOICE]
        ck("invoice_references_resolve", len(journal) == 1 and len(customer) == 2 and len(gl_rows) == 7,
           f"journal={len(journal)}, customer={len(customer)}, gl={len(gl_rows)}")
        ck("sales_amounts_exact", journal and _amount(journal[0][6]) == SALE_NET
           and _amount(customer[0][6]) == SALE_GROSS,
           f"net={journal[0][6] if journal else 'missing'}, gross={customer[0][6] if customer else 'missing'}")
        dates = [journal[0][4]] + ([issues[0][1]] if issues else []) if journal else []
        ck("sales_dates_in_fy2025", all(datetime.strptime(d, "%d.%m.%Y").year == 2025 for d in dates),
           f"dates={dates}")

    elif scheme == "cutoff":
        invoice = [r for r in _rows(root / SUBSEQUENT_INVOICES)[1:] if r[0] == CUTOFF_INVOICE]
        accrual = [r for r in gl if r[18] == CUTOFF_ACCRUAL_ENTRY]
        schedule = _rows(root / CUTOFF_EVIDENCE)
        target = [r for r in schedule[1:] if r[0] == "ABG-SYN-TARGET"]
        ck("subsequent_invoice_exact", len(invoice) == 1 and invoice[0][2] == CUTOFF_VENDOR
           and _amount(invoice[0][6]) == CUTOFF_AMOUNT,
           f"rows={len(invoice)}")
        ck("existing_accrual_exact_and_balanced", len(accrual) == 2
           and sum((_amount(r[7]) for r in accrual), Decimal("0")) == 0
           and max(_amount(r[7]) for r in accrual) == CUTOFF_ACCRUAL_AMOUNT,
           f"rows={len(accrual)}")
        expected_alloc = CUTOFF_AMOUNT if variant == "repair" else Decimal("0")
        ck("target_schedule_allocation", len(target) == 1 and _amount(target[0][9]) == expected_alloc,
           f"allocation={target[0][9] if target else 'missing'}, expected={expected_alloc}")
        allocations = sum((_amount(r[9]) for r in schedule[1:]), Decimal("0"))
        ck("schedule_ties_to_accrual", allocations == CUTOFF_ACCRUAL_AMOUNT,
           f"allocated={allocations}, accrual={CUTOFF_ACCRUAL_AMOUNT}")
        service = datetime.strptime(invoice[0][5], "%d.%m.%Y") if invoice else None
        inv_date = datetime.strptime(invoice[0][4], "%d.%m.%Y") if invoice else None
        ck("cutoff_dates_cross_year_as_expected", bool(service and inv_date
           and service.year == 2025 and inv_date.year == 2026 and service < inv_date),
           f"service={invoice[0][5] if invoice else 'missing'}, invoice={invoice[0][4] if invoice else 'missing'}")

    elif scheme == "repair_capitalization":
        masters = [r for r in _rows(root / ASSET_MASTER) if r[0] == CAP_ASSET]
        asset_tx = [r for r in _rows(root / ASSET_TX) if r[0] == CAP_ASSET and r[2] == CAP_INVOICE]
        vendor = [r for r in _rows(root / VENDOR_TX) if r[0] == CAP_VENDOR and r[1] == CAP_INVOICE]
        posting = [r for r in gl if r[14] == CAP_INVOICE and r[18] == "7708366"]
        ck("capitalized_item_references_resolve", len(masters) == 1 and len(asset_tx) == 1
           and len(vendor) == 1 and len(posting) == 3,
           f"master={len(masters)}, asset_tx={len(asset_tx)}, vendor={len(vendor)}, gl={len(posting)}")
        ck("capitalized_amounts_exact", asset_tx and vendor
           and _amount(asset_tx[0][3]) == CAP_NET and -_amount(vendor[0][6]) == CAP_GROSS,
           f"net={asset_tx[0][3] if asset_tx else 'missing'}, gross={vendor[0][6] if vendor else 'missing'}")
        assessments = _rows(root / TECH_EVIDENCE)
        expected = 1 if variant == "repair" else 0
        ck("technical_assessment_row_count", len(assessments) - 1 == expected,
           f"rows={len(assessments) - 1}, expected={expected}")
        if variant == "repair":
            ck("assessment_precedes_posting", datetime.strptime(assessments[1][1], "%d.%m.%Y")
               < datetime.strptime(CAP_POSTING_DATE, "%d.%m.%Y"),
               f"assessment={assessments[1][1]}, posting={CAP_POSTING_DATE}")
        else:
            ck("posting_date_in_fy2025", datetime.strptime(CAP_POSTING_DATE, "%d.%m.%Y").year == 2025,
               CAP_POSTING_DATE)
    return checks


def _symmetry(out: Path, scheme: str) -> list[dict[str, Any]]:
    fraud = out / f"mutated_{scheme}" / "fraud"
    repair = out / f"mutated_{scheme}" / "repair"
    same = all(_sha256(fraud / rel) == _sha256(repair / rel) for rel in CORE_FINANCIAL)
    return [{
        "name": "fraud_repair_financial_symmetry",
        "passed": same,
        "detail": ", ".join(CORE_FINANCIAL),
    }]


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    (root / "MUTATION_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify_all(data: Path, out: Path, base_hash: str,
                manifests: dict[str, dict[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    report = {}
    for scheme in SCHEMES:
        symmetry = _symmetry(out, scheme)
        report[scheme] = {"symmetry": symmetry}
        for variant in VARIANTS:
            root = out / f"mutated_{scheme}" / variant
            if manifests is not None:
                _write_manifest(root, manifests[scheme][variant])
            checks = verify_variant(root, data, scheme, variant, base_hash)
            report[scheme][variant] = checks
            if manifests is not None:
                manifest = manifests[scheme][variant]
                manifest["invariant_results"] = checks + symmetry
                manifest["all_invariants_passed"] = all(c["passed"] for c in checks + symmetry)
                _write_manifest(root, manifest)
    return report


def generate(data: Path, out: Path) -> dict[str, Any]:
    data, out = data.resolve(), out.resolve()
    if not data.is_dir():
        raise FileNotFoundError(data)
    out.mkdir(parents=True, exist_ok=True)
    base_hash = dossier_hash(data)
    manifests = {
        "fictitious_sales": _build_sales(data, out, base_hash),
        "cutoff": _build_cutoff(data, out, base_hash),
        "repair_capitalization": _build_capitalization(data, out, base_hash),
    }
    return _verify_all(data, out, base_hash, manifests)


def verify_existing(data: Path, out: Path) -> dict[str, Any]:
    data, out = data.resolve(), out.resolve()
    for scheme in SCHEMES:
        for variant in VARIANTS:
            root = out / f"mutated_{scheme}" / variant
            if not root.is_dir():
                raise FileNotFoundError(root)
    return _verify_all(data, out, dossier_hash(data))


def _summary(report: dict[str, Any]) -> tuple[bool, str]:
    lines, all_ok = [], True
    for scheme in SCHEMES:
        checks = report[scheme]["fraud"] + report[scheme]["repair"] + report[scheme]["symmetry"]
        passed = sum(c["passed"] for c in checks)
        lines.append(f"{scheme}: {passed}/{len(checks)} checks passed")
        all_ok &= passed == len(checks)
        lines.extend(f"  FAIL {c['name']}: {c['detail']}" for c in checks if not c["passed"])
    return all_ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/practice"))
    parser.add_argument("--out-root", type=Path, default=Path("data"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    report = (verify_existing(args.data, args.out_root) if args.verify_only
              else generate(args.data, args.out_root))
    ok, text = _summary(report)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
