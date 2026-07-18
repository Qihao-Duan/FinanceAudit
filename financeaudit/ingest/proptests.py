"""Property tests over the ingested build.

A-class = pipeline invariants (violation -> our bug -> exit 1).
B-class = data reconciliation expectations (violation -> recorded deviation only;
routed to Finder rule R18 as candidate material, NEVER a crash).

Placement judgment call (documented): SHA-256 / declared-sum mismatches against the
Exportprotokoll are B-class — with row counts matching, a hash mismatch is evidence
about the dossier (integrity signal), not about our parser.
"""
from __future__ import annotations

from decimal import Decimal

from .common import dec

CONTRACT_TABLES = [
    "gl", "gl_accounts", "vendors", "customers", "vendor_tx", "customer_tx",
    "assets", "asset_tx", "goods_receipts", "goods_issues", "sales_invoices",
    "purchase_invoices_2026", "subsequent_payments", "approval_log",
    "masterdata_changes", "permissions", "credit_limits", "shareholders",
    "trial_balance", "op_debitors_accounts", "op_debitors_items",
    "op_creditors_accounts", "reconciliation",
]

GDPDU_TXT_FILES = [
    "Sachkonten/Sachkonten.txt", "Sachkonten/Sachkontobuchungen.txt",
    "Debitoren/Kunden.txt", "Debitoren/Kundenbuchungen.txt",
    "Kreditoren/Lieferanten.txt", "Kreditoren/Lieferantenbuchungen.txt",
    "AV/Anlagen.txt", "AV/Anlagenbuchungen.txt",
]


def _a(name, passed, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


def _b(name, passed, deviation, detail):
    return {"name": name, "passed": bool(passed), "deviation": deviation, "detail": detail}


def _fmt(d: Decimal) -> str:
    return f"{d:,.2f}"


def run_property_tests(con, tables, manifest_rows, protokoll, protokoll_errors):
    a_class, b_class = [], []
    man = {m["file"]: m for m in manifest_rows}

    # ---------------------------------------------------------------- A-class
    # A1: parsed rows == Exportprotokoll declared counts (fallback: structural)
    details, ok = [], True
    for f in GDPDU_TXT_FILES:
        parsed = man.get(f, {}).get("parsed_units")
        declared = protokoll.get(f, {}).get("declared_rows")
        if declared is None:
            details.append(f"{f}: parsed={parsed}, no declared count (protokoll gap)")
            continue
        good = parsed == declared
        ok &= good
        details.append(f"{f}: parsed={parsed} declared={declared} {'OK' if good else 'FAIL'}")
    a_class.append(_a("gdpdu_rowcounts_vs_exportprotokoll", ok, "; ".join(details)))

    # A2: every GDPdU row had exactly the declared number of fields + no mapper errors
    bad = {f: man[f]["parser_errors"] for f in GDPDU_TXT_FILES
           if f in man and man[f]["parser_errors"]}
    a_class.append(_a("gdpdu_field_count_and_typing_errors", not bad,
                      "no parser errors" if not bad else str(bad)))

    # A3: dual-path aggregation of the GL total (DuckDB DOUBLE vs python Decimal)
    duck_total = con.execute("SELECT ROUND(SUM(amount), 2) FROM gl").fetchone()[0]
    dec_total = sum((dec(r["amount_dec"]) for r in tables["gl"]), Decimal(0))
    match = Decimal(str(duck_total)).quantize(Decimal("0.01")) == dec_total.quantize(Decimal("0.01"))
    a_class.append(_a("dual_path_gl_total", match,
                      f"duckdb SUM(amount)={duck_total}, Decimal sum(amount_dec)={_fmt(dec_total)}"))

    # A4: amount typing — DOUBLE column vs Decimal(raw) never deviates > 0.005
    worst = Decimal(0)
    for t in ("gl", "vendor_tx", "customer_tx", "asset_tx"):
        for r in tables[t]:
            if r.get("amount") is None:
                if r.get("amount_dec"):
                    worst = Decimal("1")  # raw present but typed value lost
                continue
            d = abs(Decimal(repr(r["amount"])) - dec(r["amount_dec"]))
            worst = max(worst, d)
    a_class.append(_a("amount_typing_double_vs_decimal", worst <= Decimal("0.005"),
                      f"max |DOUBLE - Decimal(raw)| = {worst}"))

    # A5: join cardinality — every table row carries a source_id resolving 1:1 in registry
    details, ok = [], True
    for t in CONTRACT_TABLES:
        n, n_sid, n_join = con.execute(
            f"""SELECT (SELECT COUNT(*) FROM {t}),
                       (SELECT COUNT(source_id) FROM {t}),
                       (SELECT COUNT(*) FROM {t} x JOIN source_registry r USING (source_id))
            """).fetchone()
        good = n == n_sid == n_join
        ok &= good
        if not good:
            details.append(f"{t}: rows={n} with_sid={n_sid} joined={n_join}")
    a_class.append(_a("registry_join_cardinality", ok,
                      "all tables join source_registry 1:1" if ok else "; ".join(details)))

    # A6: registry primary key unique
    n, nd = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT source_id) FROM source_registry").fetchone()
    a_class.append(_a("registry_pk_unique", n == nd, f"rows={n} distinct={nd}"))

    # ---------------------------------------------------------------- B-class
    # B1: GL zero-sum
    total = sum((dec(r["amount_dec"]) for r in tables["gl"]), Decimal(0))
    soll = sum((dec(r["amount_dec"]) for r in tables["gl"] if dec(r["amount_dec"]) > 0), Decimal(0))
    b_class.append(_b("gl_zero_sum", total == 0, float(abs(total)),
                      f"GL total={_fmt(total)} (Soll volume={_fmt(soll)})"))

    # B2: per-entry_id balance
    groups = {}
    for r in tables["gl"]:
        groups[r["entry_id"]] = groups.get(r["entry_id"], Decimal(0)) + dec(r["amount_dec"])
    unbal = {k: v for k, v in groups.items() if v != 0}
    worst5 = sorted(unbal.items(), key=lambda kv: -abs(kv[1]))[:5]
    b_class.append(_b("gl_entry_balance", not unbal,
                      float(sum(abs(v) for v in unbal.values())),
                      f"{len(groups)} entry groups, {len(unbal)} unbalanced"
                      + (f"; worst: {[(k, _fmt(v)) for k, v in worst5]}" if worst5 else "")))

    # B3/B4: AR / AP tie-out: subledger == GL control account == OP list == trial balance
    tb = {r["account"]: dec(r["closing_dec"]) for r in tables["trial_balance"]}

    def tieout(name, sub_table, control, op_table):
        sub = sum((dec(r["amount_dec"]) for r in tables[sub_table]), Decimal(0))
        gl_c = sum((dec(r["amount_dec"]) for r in tables["gl"] if r["hb_account"] == control),
                   Decimal(0))
        op = sum((dec(r["balance_dec"]) for r in tables[op_table]), Decimal(0))
        tb_c = tb.get(control, Decimal(0))
        vals = [sub, gl_c, op, tb_c]
        dev = float(max(vals) - min(vals))
        b_class.append(_b(name, dev == 0, dev,
                          f"{sub_table}={_fmt(sub)} | GL {control}={_fmt(gl_c)} | "
                          f"{op_table}={_fmt(op)} | trial_balance {control}={_fmt(tb_c)}"))

    tieout("ar_tieout_subledger_gl_op_tb", "customer_tx", "230000", "op_debitors_accounts")
    tieout("ap_tieout_subledger_gl_op_tb", "vendor_tx", "330000", "op_creditors_accounts")

    # B5: asset account-level reconciliation (no per-asset opening values in this dossier
    #     -> degraded to account level per PLAN §3.1: GL Vortrag + moves == closing,
    #     cross-checked against asset_tx moves and trial balance)
    av_accounts = sorted({r["grp"] for r in tables["asset_tx"] if r["grp"]})
    details, worst_dev = [], Decimal(0)
    for acct in av_accounts:
        opening = sum((dec(r["amount_dec"]) for r in tables["gl"]
                       if r["hb_account"] == acct and r["posting_type"] == "Vortrag"), Decimal(0))
        moves_gl = sum((dec(r["amount_dec"]) for r in tables["gl"]
                        if r["hb_account"] == acct and r["posting_type"] != "Vortrag"), Decimal(0))
        closing_gl = opening + moves_gl
        moves_av = sum((dec(r["amount_dec"]) for r in tables["asset_tx"] if r["grp"] == acct),
                       Decimal(0))
        tb_c = tb.get(acct, Decimal(0))
        d1, d2 = abs(moves_gl - moves_av), abs(closing_gl - tb_c)
        worst_dev = max(worst_dev, d1, d2)
        details.append(f"{acct}: opening={_fmt(opening)} moves_gl={_fmt(moves_gl)} "
                       f"moves_assetledger={_fmt(moves_av)} closing_gl={_fmt(closing_gl)} "
                       f"tb={_fmt(tb_c)} dev_moves={_fmt(d1)} dev_closing={_fmt(d2)}")
    b_class.append(_b("asset_account_level_reconciliation", worst_dev == 0, float(worst_dev),
                      " || ".join(details) + " || note: per-asset opening book values not in "
                      "dossier -> full roll-forward not executable (coverage limitation)"))

    # B6: declared sums in the Exportprotokoll vs parsed Decimal sums
    sum_map = {"Sachkonten/Sachkontobuchungen.txt": "gl",
               "Debitoren/Kundenbuchungen.txt": "customer_tx",
               "Kreditoren/Lieferantenbuchungen.txt": "vendor_tx",
               "AV/Anlagenbuchungen.txt": "asset_tx"}
    details, worst_dev = [], Decimal(0)
    for f, t in sum_map.items():
        declared = protokoll.get(f, {}).get("declared_sum_dec")
        if declared is None:
            details.append(f"{f}: no declared sum parsed")
            continue
        got = sum((dec(r["amount_dec"]) for r in tables[t]), Decimal(0))
        d = abs(got - Decimal(declared))
        worst_dev = max(worst_dev, d)
        details.append(f"{t}: parsed={_fmt(got)} declared={_fmt(Decimal(declared))}")
    b_class.append(_b("export_declared_sums", worst_dev == 0, float(worst_dev), "; ".join(details)))

    # B7: SHA-256 of the 8 txt files vs Exportprotokoll
    details, n_ok = [], 0
    for f in GDPDU_TXT_FILES:
        declared = protokoll.get(f, {}).get("declared_sha256")
        computed = man.get(f, {}).get("file_hash")
        good = declared is not None and computed == declared
        n_ok += good
        if not good:
            details.append(f"{f}: computed={computed} declared={declared}")
    b_class.append(_b("sha256_vs_exportprotokoll", n_ok == len(GDPDU_TXT_FILES),
                      float(len(GDPDU_TXT_FILES) - n_ok),
                      f"{n_ok}/{len(GDPDU_TXT_FILES)} hashes match"
                      + ("" if not details else "; " + "; ".join(details))))
    if protokoll_errors:
        b_class.append(_b("exportprotokoll_parse", False, float(len(protokoll_errors)),
                          "; ".join(protokoll_errors)))

    # B8: approval-log entry ids all resolve in GL (also validates the entry_id alias)
    ap = {r["entry_id"] for r in tables["approval_log"] if r["entry_id"]}
    hit = ap & set(groups)
    b_class.append(_b("approval_log_entry_ids_in_gl", len(hit) == len(ap),
                      float(len(ap) - len(hit)),
                      f"{len(hit)}/{len(ap)} approval-log ERFASSUNGSNUMMER appear as gl.entry_id"))

    return a_class, b_class
