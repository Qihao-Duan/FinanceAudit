"""Ingest stage CLI: python3 -m financeaudit.ingest.run [--data data/practice --out build]

Produces build/audit.duckdb + build/manifest.json + build/profiles.json +
build/property_tests.json per docs/CONTRACTS.md §2. Exit 1 on A-class failure.
Deterministic — no LLM involved (manifest carries llm_used: false).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from financeaudit.core import config
from .common import ManifestBuilder, SourceRegistry
from .gdpdu import GDPDU_TABLE_SCHEMAS, parse_gdpdu
from .profiler import profile_all
from .proptests import run_property_tests
from .sidecars import (
    CSV_TABLE_SCHEMAS,
    XLSX_TABLE_SCHEMAS,
    parse_exportprotokoll,
    parse_sidecars,
)

# contract-first column orders (extras appended automatically)
ORDER = {
    "gl": ["entry_id", "line_no", "account", "hb_account", "sub_account", "amount",
           "currency", "text", "posting_date", "doc_date", "doc_ref", "journal_ref",
           "period_code", "posting_type", "user_id", "entry_date", "entry_time",
           "festschreibung", "row_id", "source_id"],
    "vendors": ["account", "ustid", "street", "plz", "city", "country", "name", "grp",
                "vat_grp", "currency", "row_id", "source_id"],
    "customers": ["account", "ustid", "street", "plz", "city", "country", "name", "grp",
                  "vat_grp", "currency", "row_id", "source_id"],
    "vendor_tx": ["account", "posting_date", "doc_ref", "doc_date", "text", "amount",
                  "currency", "module_tag", "status", "row_id", "source_id"],
    "customer_tx": ["account", "posting_date", "doc_ref", "doc_date", "text", "amount",
                    "currency", "module_tag", "status", "row_id", "source_id"],
    "assets": ["asset_id", "name", "grp", "typ", "status", "row_id", "source_id"],
    "asset_tx": ["target", "value_date", "doc_ref", "amount", "kind", "text", "grp",
                 "row_id", "source_id"],
}

DATE_COLS = {
    "gl": ["posting_date", "doc_date", "entry_date"],
    "vendor_tx": ["posting_date", "doc_date"],
    "customer_tx": ["posting_date", "doc_date"],
    "asset_tx": ["value_date"],
    "goods_receipts": ["we_date"],
    "goods_issues": ["wa_date"],
    "sales_invoices": ["invoice_date", "service_date"],
    "purchase_invoices_2026": ["invoice_date", "service_date"],
    "subsequent_payments": ["posting_date"],
    "approval_log": ["created_date", "approval_date"],
    "masterdata_changes": ["change_date"],
    "op_debitors_items": ["doc_date"],
}
INT_COLS = {
    "gl": ["line_no"], "approval_log": ["n_lines"],
    "source_registry": ["row_no", "page_index", "para_no"],
    "doc_units": ["page_index", "para_no"],
    "column_profiles": ["position", "n_unique"],
    "source_manifest": ["size_bytes", "expected_units", "parsed_units"],
}
BOOL_COLS = {
    "permissions": ["can_post", "can_approve", "can_pay", "can_create_vendor",
                    "can_periods", "is_admin", "is_mgmt"],
    "shareholders": ["is_section_header"],
    "trial_balance": ["closing_computed"],
    "column_profiles": ["is_constant"],
}
DOUBLE_COLS = {
    "gl": ["amount"], "vendor_tx": ["amount"], "customer_tx": ["amount"],
    "asset_tx": ["amount"], "goods_receipts": ["amount"], "goods_issues": ["amount"],
    "sales_invoices": ["amount"], "purchase_invoices_2026": ["amount"],
    "subsequent_payments": ["amount"], "approval_log": ["sum_abs"],
    "credit_limits": ["credit_limit", "utilization"], "shareholders": ["share_pct"],
    "trial_balance": ["opening", "debit", "credit", "closing"],
    "reconciliation": ["value"], "op_debitors_accounts": ["balance"],
    "op_creditors_accounts": ["balance"], "op_debitors_items": ["amount"],
    "column_profiles": ["null_rate"],
}


def write_table(con, name, rows):
    if not rows:
        # ROBUSTNESS (S2/S4/S8/S9 absent/empty table): create the empty table with its FULL
        # declared schema so downstream rules resolve every column (0 rows) instead of a
        # Binder Error. Prefer the full derived schemas over ORDER (a partial ordering hint
        # that omits raw_*/*_dec columns); ORDER is only a last resort for internal tables.
        cols = (GDPDU_TABLE_SCHEMAS.get(name) or CSV_TABLE_SCHEMAS.get(name)
                or XLSX_TABLE_SCHEMAS.get(name) or ORDER.get(name) or ["row_id", "source_id"])
        df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(rows)
        order = [c for c in ORDER.get(name, []) if c in df.columns]
        rest = [c for c in df.columns if c not in order]
        df = df[order + rest]
    dates = [c for c in DATE_COLS.get(name, []) if c in df.columns]
    for c in dates:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in INT_COLS.get(name, []):
        if c in df.columns:
            df[c] = df[c].astype("Int64")
    if "row_id" in df.columns:
        df["row_id"] = df["row_id"].astype("Int64")
    for c in BOOL_COLS.get(name, []):
        if c in df.columns:
            df[c] = df[c].astype("boolean")
    for c in DOUBLE_COLS.get(name, []):
        if c in df.columns:
            df[c] = df[c].astype("float64")
    con.register("df_tmp", df)
    typed = set(dates) | set(INT_COLS.get(name, [])) | set(BOOL_COLS.get(name, [])) \
        | set(DOUBLE_COLS.get(name, [])) | {"row_id"}
    sel = ", ".join(
        f'CAST("{c}" AS DATE) AS "{c}"' if c in dates
        else (f'"{c}"' if c in typed else f'CAST("{c}" AS VARCHAR) AS "{c}"')
        for c in df.columns
    )
    con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT {sel} FROM df_tmp')
    con.unregister("df_tmp")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="financeaudit.ingest.run")
    ap.add_argument("--data", default=str(config.DATA_DIR))
    ap.add_argument("--out", default=str(config.BUILD_DIR))
    args = ap.parse_args(argv)
    data_dir, out_dir = Path(args.data), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = SourceRegistry()
    manifest = ManifestBuilder()

    protokoll, protokoll_errors = parse_exportprotokoll(data_dir)
    declared_counts = {f: e["declared_rows"] for f, e in protokoll.items()}

    tables, gdpdu_feed = parse_gdpdu(data_dir, registry, manifest, declared_counts)
    side_tables, doc_units, sidecar_feed = parse_sidecars(data_dir, registry, manifest)
    tables.update(side_tables)

    cross = {
        "approval_entry_ids": {r["entry_id"] for r in tables["approval_log"] if r["entry_id"]},
        "sales_invoice_nos": {r["invoice_no"] for r in tables["sales_invoices"] if r["invoice_no"]},
        "purchase_doc_refs": (
            {r["invoice_ref"] for r in tables["goods_receipts"] if r["invoice_ref"]}
            | {r["invoice_no"] for r in tables["purchase_invoices_2026"] if r["invoice_no"]}
        ),
    }
    profiles, conflicts = profile_all(gdpdu_feed, sidecar_feed, cross)

    # ------------------------------------------------------------- write DuckDB
    db_path = out_dir / "audit.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for name, rows in tables.items():
        write_table(con, name, rows)
    write_table(con, "source_registry", registry.rows)
    write_table(con, "doc_units", doc_units)
    write_table(con, "column_profiles", profiles)
    manifest_table_rows = [
        {**m, "parser_errors": json.dumps(m["parser_errors"], ensure_ascii=False)}
        for m in manifest.rows
    ]
    write_table(con, "source_manifest", manifest_table_rows)

    # ---------------------------------------------------------- property tests
    a_class, b_class = run_property_tests(con, tables, manifest.rows, protokoll,
                                          protokoll_errors)
    con.close()

    # ------------------------------------------------------------ JSON outputs
    hash_by_file = {m["file"]: m["file_hash"] for m in manifest.rows}
    proto_entries = {
        f: {**e, "sha256_match": hash_by_file.get(f) == e.get("declared_sha256")}
        for f, e in protokoll.items()
    }
    manifest_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extractor_version": config.EXTRACTOR_VERSION,
        "llm_used": False,
        "data_dir": str(data_dir),
        "exportprotokoll": {"parse_errors": protokoll_errors, "entries": proto_entries},
        "semantic_conflicts": conflicts,
        "notes": [
            "P0: GL columns declared ERFASSUNGSNUMMER/JOURNALZEILE are empty; the column "
            "declared GEGENKONTO holds journal-entry ids and is aliased to gl.entry_id "
            "(validated: pattern share, per-group zero balance, approval-log overlap).",
            "NO usable counter-account column exists in this dossier — counter-account "
            "checks must use within-entry account co-occurrence (PLAN rule R9).",
            "Subledger clearing columns (LETZTER_AUSGLEICH*) are empty and module tags are "
            "constant — payment identification must use GL BUCHUNGSTYP=='Zahlung'.",
        ],
        "files": manifest.rows,
        "tables": {name: len(rows) for name, rows in tables.items()},
        "registry_units": len(registry.rows),
        "doc_units": len(doc_units),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest_json, ensure_ascii=False, indent=2, default=str), "utf-8")
    (out_dir / "profiles.json").write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), "utf-8")
    prop = {"a_class": a_class, "b_class": b_class}
    (out_dir / "property_tests.json").write_text(
        json.dumps(prop, ensure_ascii=False, indent=2), "utf-8")

    # ------------------------------------------------------------------ report
    print(f"[ingest] wrote {db_path}")
    for name in sorted(tables):
        print(f"  table {name:26s} {len(tables[name]):>7,} rows")
    print(f"  table {'source_registry':26s} {len(registry.rows):>7,} rows")
    print(f"  table {'doc_units':26s} {len(doc_units):>7,} rows")
    print(f"  table {'column_profiles':26s} {len(profiles):>7,} rows")
    print(f"  table {'source_manifest':26s} {len(manifest.rows):>7,} rows")
    print("[ingest] A-class property tests:")
    for t in a_class:
        print(f"  {'PASS' if t['passed'] else 'FAIL'} {t['name']}: {t['detail'][:200]}")
    print("[ingest] B-class reconciliation expectations:")
    for t in b_class:
        flag = "ok  " if t["passed"] else "DEV "
        print(f"  {flag} {t['name']} (deviation={t['deviation']}): {t['detail'][:200]}")
    failed = [t["name"] for t in a_class if not t["passed"]]
    if failed:
        print(f"[ingest] A-CLASS FAILURE -> exit 1: {failed}", file=sys.stderr)
        return 1
    print("[ingest] all A-class invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
