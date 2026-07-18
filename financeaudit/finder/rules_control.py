"""Finder control rules R1-R8 (Agent B) — PLAN §3.2 channel A (+R8 statistical).

Each rule: fn(con: duckdb.Connection, thresholds: dict) -> list[candidate_dict]
Candidate schema per CONTRACTS.md §3 (candidate_id assigned by finder.run).

All € logic runs on the semantic-alias GL schema (entry_id = declared-GEGENKONTO
column, see PLAN §3.1 P0). Descriptions are neutral one-sentence statements —
no fraud wording (verdicts belong to later stages).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import duckdb

from financeaudit.finder.thresholds import lock_date_value, param, tcite_source_id, tval

FISCAL_YEAR_END = date(2025, 12, 31)  # derived from dossier period 01-12/2025


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fmt_eur(x: float) -> str:
    return f"{x:,.2f} EUR"


def _vendor_names(con: duckdb.DuckDBPyConnection) -> Dict[str, str]:
    return {a: n for a, n in con.execute("SELECT account, name FROM vendors").fetchall()}


def _candidate(rule_id: str, rule_name: str, channel: str, anomaly_type: str,
               risk_tier: str, entity_key: str, entity_label: str,
               entry_ids: List[str], source_ids: List[str],
               metrics: Dict[str, Any], denominators: Dict[str, Any],
               description: str) -> Dict[str, Any]:
    # dedupe source_ids, preserve order
    seen, sids = set(), []
    for s in source_ids:
        if s and s not in seen:
            seen.add(s)
            sids.append(s)
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "channel": channel,
        "anomaly_type": anomaly_type,
        "risk_tier": risk_tier,
        "entity_key": entity_key,
        "entity_label": entity_label,
        "entry_ids": sorted(set(e for e in entry_ids if e)),
        "source_ids": sids,
        "metrics": metrics,
        "denominators": denominators,
        "description": description,
    }


# --------------------------------------------------------------------------
# R1 — Segregation-of-duties join: rights held x rights enacted
# --------------------------------------------------------------------------

def rule_r01_sod(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    perms = con.execute(
        """SELECT user_id, department, can_post, can_approve, can_pay,
                  can_create_vendor, is_admin, is_mgmt, note, source_id
           FROM permissions"""
    ).fetchall()
    n_users = len(perms)
    vendor_names = _vendor_names(con)

    holders = [p for p in perms
               if bool(p[2]) and bool(p[4]) and bool(p[5])]  # post AND pay AND create_vendor

    out: List[Dict[str, Any]] = []
    for (user_id, dept, can_post, can_approve, can_pay, can_create, is_admin,
         is_mgmt, note, perm_sid) in holders:
        # what the user actually did
        creations = con.execute(
            """SELECT account, name, change_date, field, source_id
               FROM masterdata_changes
               WHERE changed_by = ? AND field LIKE 'Neuanlage%'""",
            [user_id],
        ).fetchall()
        n_postings = con.execute(
            "SELECT count(DISTINCT entry_id) FROM gl WHERE user_id = ?", [user_id]
        ).fetchone()[0]
        payments = con.execute(
            """SELECT entry_id, sub_account, amount, posting_date, doc_ref, source_id
               FROM gl
               WHERE user_id = ? AND posting_type = 'Zahlung'
                 AND hb_account = '330000' AND sub_account IS NOT NULL""",
            [user_id],
        ).fetchall()

        created_accounts = {c[0] for c in creations}
        paid_accounts = {p[1] for p in payments}
        overlap = sorted(created_accounts & paid_accounts)

        # rows on created vendors that this same user also posted/paid
        overlap_rows = []
        if created_accounts:
            overlap_rows = con.execute(
                """SELECT entry_id, source_id FROM gl
                   WHERE user_id = ? AND sub_account IN ({})""".format(
                    ",".join("?" * len(created_accounts))),
                [user_id, *sorted(created_accounts)],
            ).fetchall()

        enacted_all = bool(creations) and n_postings > 0 and bool(payments)
        if enacted_all and overlap:
            tier = "high"
        elif enacted_all:
            tier = "medium"
        else:
            tier = "low"

        entry_ids = [r[0] for r in overlap_rows] or [p[0] for p in payments[:10]]
        source_ids = [perm_sid] + [c[4] for c in creations] + [r[1] for r in overlap_rows]
        if not overlap_rows:
            source_ids += [p[5] for p in payments[:10]]

        if enacted_all and overlap:
            v = overlap[0]
            desc = (f"User {user_id} holds vendor-create, posting and payment rights and "
                    f"exercised all three on the same vendor account {v} "
                    f"({vendor_names.get(v, 'unknown')}), which the audit plan's "
                    f"segregation-of-duties provision assigns to separate functions.")
        elif enacted_all:
            desc = (f"User {user_id} holds vendor-create, posting and payment rights and "
                    f"used each of them during 2025, although not demonstrably on the "
                    f"same vendor account.")
        else:
            desc = (f"User {user_id} ({dept}) holds the combination of vendor-create, "
                    f"posting and payment rights foreseen for separate functions; no "
                    f"combined use on a single vendor was observed in the data provided.")

        out.append(_candidate(
            "R01", "sod_rights_and_enactment", "control", "rule_based", tier,
            f"user:{user_id}", f"{user_id} ({dept})",
            entry_ids, source_ids,
            {
                "rights": {"can_post": bool(can_post), "can_pay": bool(can_pay),
                           "can_create_vendor": bool(can_create),
                           "can_approve": bool(can_approve),
                           "is_admin": bool(is_admin), "is_mgmt": bool(is_mgmt)},
                "note": note,
                "n_vendor_creations": len(creations),
                "vendors_created": sorted(created_accounts),
                "n_entries_posted": int(n_postings),
                "n_vendor_payments": len(payments),
                "created_and_paid_same_vendor": overlap,
            },
            {"population_size": n_users, "rule_hits": len(holders),
             "peers_with_expected_evidence": 0},
            desc,
        ))
    return out


# --------------------------------------------------------------------------
# R2 — Self-approval in master-data changes
# --------------------------------------------------------------------------

def rule_r02_self_approval(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = con.execute(
        """SELECT change_date, kind, account, name, field, changed_by, approved_by,
                  approved, source_id
           FROM masterdata_changes
           WHERE changed_by IS NOT NULL AND approved_by IS NOT NULL
             AND changed_by = approved_by"""
    ).fetchall()
    n_changes = con.execute("SELECT count(*) FROM masterdata_changes").fetchone()[0]

    out = []
    for change_date, kind, account, name, field, changed_by, approved_by, approved, sid in rows:
        tier = "high" if field and field.lower().startswith("neuanlage") else "medium"
        ek = f"vendor:{account}" if (kind or "").lower().startswith("kredit") else f"account:{account}"
        out.append(_candidate(
            "R02", "masterdata_self_approval", "control", "rule_based", tier,
            ek, f"{name} ({account})",
            [], [sid],
            {
                "change_date": str(change_date), "field": field, "kind": kind,
                "changed_by": changed_by, "approved_by": approved_by,
                "approved_flag": approved,
            },
            {"population_size": int(n_changes), "rule_hits": len(rows),
             "peers_with_expected_evidence": 0},
            (f"The master-data change '{field}' for {name} ({account}) dated "
             f"{change_date} was entered and approved by the same user "
             f"{changed_by}, although such changes are subject to approval by a "
             f"second person."),
        ))
    return out


# --------------------------------------------------------------------------
# R3 — Threshold splitting (GL BUCHUNGSTYP=='Zahlung' only; tiered)
# --------------------------------------------------------------------------

def rule_r03_threshold_splitting(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    limit = float(tval(thresholds, "approval_limit_eur"))
    eps = float(param(thresholds, "near_limit_epsilon_eur"))
    window = int(param(thresholds, "split_window_days"))
    limit_sid = tcite_source_id(thresholds, "approval_limit_eur")
    vendor_names = _vendor_names(con)

    pays = con.execute(
        """SELECT entry_id, sub_account, doc_ref, posting_date, amount, user_id, source_id
           FROM gl
           WHERE posting_type = 'Zahlung' AND hb_account = '330000'
             AND sub_account IS NOT NULL
           ORDER BY sub_account, posting_date"""
    ).fetchall()
    population = len(pays)

    out: List[Dict[str, Any]] = []
    consumed = set()  # (entry_id) already in a high-tier cluster

    # --- Tier HIGH: same underlying debt (same doc_ref) split below the limit
    by_debt: Dict[tuple, list] = defaultdict(list)
    for row in pays:
        entry_id, sub, doc_ref, pdate, amount, user, sid = row
        if doc_ref:
            by_debt[(sub, doc_ref)].append(row)
    for (sub, doc_ref), rows in sorted(by_debt.items()):
        if len(rows) < 2:
            continue
        amounts = [r[4] for r in rows]
        if not all(0 < a < limit for a in amounts):
            continue
        total = sum(amounts)
        if total <= limit:
            continue
        dates = sorted(r[3] for r in rows)
        span = (dates[-1] - dates[0]).days
        if span > window:
            continue
        users = sorted({r[5] for r in rows})
        for r in rows:
            consumed.add(r[0])
        out.append(_candidate(
            "R03", "threshold_splitting", "control", "rule_based", "high",
            f"vendor:{sub}", vendor_names.get(sub, sub),
            [r[0] for r in rows],
            [r[6] for r in rows] + ([limit_sid] if limit_sid else []),
            {
                "n_payments": len(rows),
                "amounts": amounts,
                "sum": round(total, 2),
                "limit": limit,
                "doc_ref": doc_ref,
                "date_span_days": span,
                "dates": [str(d) for d in dates],
                "user_ids": users,
                "cluster_kind": "same_doc_ref",
            },
            {"population_size": population, "rule_hits": 0,
             "peers_with_expected_evidence": 0},
            (f"{len(rows)} payments of {_fmt_eur(min(amounts))}–{_fmt_eur(max(amounts))} "
             f"referencing the same document {doc_ref} were posted to vendor {sub} "
             f"({vendor_names.get(sub, 'unknown')}) "
             + (f"on {dates[0]}" if span == 0 else f"within {span} day(s)")
             + f", each below the {_fmt_eur(limit)} dual-approval limit while "
               f"totalling {_fmt_eur(total)}."),
        ))

    # --- Tier MEDIUM: same vendor, same day, several near-limit payments on
    #     DIFFERENT debts (doc_refs), combined above the limit
    by_day: Dict[tuple, list] = defaultdict(list)
    for row in pays:
        if row[0] in consumed:
            continue
        by_day[(row[1], row[3])].append(row)
    for (sub, pdate), rows in sorted(by_day.items()):
        near = [r for r in rows if limit - eps <= r[4] < limit]
        if len(near) < 2:
            continue
        doc_refs = {r[2] for r in near}
        if len(doc_refs) < 2:
            continue  # same-debt handled above
        total = sum(r[4] for r in near)
        if total <= limit:
            continue
        out.append(_candidate(
            "R03", "threshold_splitting", "control", "rule_based", "medium",
            f"vendor:{sub}", vendor_names.get(sub, sub),
            [r[0] for r in near],
            [r[6] for r in near] + ([limit_sid] if limit_sid else []),
            {
                "n_payments": len(near),
                "amounts": [r[4] for r in near],
                "sum": round(total, 2),
                "limit": limit,
                "epsilon": eps,
                "doc_refs": sorted(doc_refs),
                "posting_date": str(pdate),
                "user_ids": sorted({r[5] for r in near}),
                "cluster_kind": "same_vendor_same_day_near_limit",
            },
            {"population_size": population, "rule_hits": 0,
             "peers_with_expected_evidence": 0},
            (f"{len(near)} payments within {_fmt_eur(eps)} below the {_fmt_eur(limit)} "
             f"approval limit were posted to vendor {sub} "
             f"({vendor_names.get(sub, 'unknown')}) on {pdate} against different "
             f"documents, totalling {_fmt_eur(total)}."),
        ))
    # NOTE: different vendors are never aggregated; independent normal invoices
    # on the same day produce no candidate by construction.

    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# --------------------------------------------------------------------------
# R4 — Cut-off: post-period invoices with prior-year service dates
# --------------------------------------------------------------------------

def rule_r04_cutoff(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    invoices = con.execute(
        """SELECT invoice_no, vendor_account, vendor_name, invoice_date,
                  service_date, amount, note, source_id
           FROM purchase_invoices_2026
           WHERE service_date IS NOT NULL AND service_date <= ?
           ORDER BY invoice_no""",
        [FISCAL_YEAR_END],
    ).fetchall()
    n_pop = con.execute("SELECT count(*) FROM purchase_invoices_2026").fetchone()[0]
    if not invoices:
        return []

    receipts = con.execute(
        """SELECT we_no, we_date, invoice_ref, vendor_account, vendor_name,
                  amount, note, source_id
           FROM goods_receipts
           WHERE lower(coalesce(note,'')) LIKE '%rechnung offen%'"""
    ).fetchall()

    accruals = con.execute(
        """SELECT entry_id, line_no, account, amount, text, posting_date, doc_ref, source_id
           FROM gl
           WHERE posting_type <> 'Vortrag'
             AND (lower(text) LIKE '%unfaktur%' OR lower(text) LIKE '%abgrenzung%')
           ORDER BY entry_id, line_no"""
    ).fetchall()
    accrual_entries = sorted({a[0] for a in accruals})
    # expense-side amount of the accrual (positive leg)
    accrual_amount = round(sum(a[3] for a in accruals if a[3] > 0), 2)

    # invoice <-> goods-receipt matching on vendor_account + amount
    gr_index: Dict[tuple, list] = defaultdict(list)
    for gr in receipts:
        gr_index[(gr[3], round(gr[5], 2))].append(gr)
    matches, unmatched_grs = [], {gr[0] for gr in receipts}
    for inv in invoices:
        key = (inv[1], round(inv[5], 2))
        hit = gr_index[key].pop(0) if gr_index[key] else None
        matches.append((inv, hit))
        if hit:
            unmatched_grs.discard(hit[0])

    # subset-sum: does any exact subset of the invoice amounts equal the accrual?
    cents = [int(round(inv[5] * 100)) for inv in invoices]
    target = int(round(accrual_amount * 100))
    reachable = {0}
    for c in cents:
        reachable |= {r + c for r in reachable}
    subset_match = target in reachable and target > 0

    total = round(sum(inv[5] for inv in invoices), 2)
    n_matched = sum(1 for _, gr in matches if gr is not None)
    vendors = sorted({inv[1] for inv in invoices})

    source_ids = ([inv[7] for inv in invoices]
                  + [gr[7] for _, gr in matches if gr is not None]
                  + [a[7] for a in accruals])
    lower_bound = round(total - accrual_amount, 2) if not subset_match else 0.0

    return [_candidate(
        "R04", "cutoff_unaccrued_liabilities", "control", "rule_based", "high",
        "period:2025-cutoff", "FY2025 year-end cut-off (purchase invoices)",
        accrual_entries,
        source_ids,
        {
            "n_invoices": len(invoices),
            "invoices_total": total,
            "invoice_nos": [inv[0] for inv in invoices],
            "vendor_accounts": vendors,
            "service_date_range": [str(min(i[4] for i in invoices)),
                                   str(max(i[4] for i in invoices))],
            "invoice_date_range": [str(min(i[3] for i in invoices)),
                                   str(max(i[3] for i in invoices))],
            "n_goods_receipts_open": len(receipts),
            "n_invoice_gr_matches": n_matched,
            "accrual_entry_ids": accrual_entries,
            "accrual_amount": accrual_amount,
            "accrual_subset_sum_match": bool(subset_match),
            "potential_unmatched_liability_range": [lower_bound, total],
        },
        {"population_size": int(n_pop), "rule_hits": 1,
         "peers_with_expected_evidence": n_matched},
        (f"{len(invoices)} purchase invoices recorded in January 2026 with "
         f"service dates in December 2025 total {_fmt_eur(total)} and correspond "
         f"to {n_matched} goods receipts marked 'Rechnung offen', while the "
         f"recorded year-end accrual for unbilled services amounts to "
         f"{_fmt_eur(accrual_amount)} and no exact subset of the invoices "
         f"reproduces that amount."),
    )]


# --------------------------------------------------------------------------
# R5 — Post-lock entries (entry_date after Festschreibung)
# --------------------------------------------------------------------------

def rule_r05_post_lock(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    lock = lock_date_value(thresholds)
    if lock is None:
        return []
    lock_sid = tcite_source_id(thresholds, "lock_date")
    n_pop = con.execute("SELECT count(DISTINCT entry_id) FROM gl").fetchone()[0]
    rows = con.execute(
        """SELECT entry_id, min(entry_date) ed, min(posting_date) pd,
                  min(user_id) uid, count(*) n, list(source_id) sids,
                  min(text) t
           FROM gl WHERE entry_date > ?
           GROUP BY entry_id ORDER BY entry_id""",
        [lock],
    ).fetchall()
    out = []
    for entry_id, ed, pd, uid, n, sids, text in rows:
        out.append(_candidate(
            "R05", "post_lock_entry", "control", "rule_based", "high",
            f"entry:{entry_id}", f"Entry {entry_id} ({text})",
            [entry_id],
            list(sids) + ([lock_sid] if lock_sid else []),
            {"entry_date": str(ed), "posting_date": str(pd), "user_id": uid,
             "n_lines": int(n), "lock_date": str(lock)},
            {"population_size": int(n_pop), "rule_hits": len(rows),
             "peers_with_expected_evidence": 0},
            (f"Journal entry {entry_id} was recorded on {ed}, after the "
             f"Festschreibung date {lock} stated in the IT confirmation."),
        ))
    return out


# --------------------------------------------------------------------------
# R6 — New-vendor fast-pay
# --------------------------------------------------------------------------

def rule_r06_new_vendor_fast_pay(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    fast_create = int(param(thresholds, "fast_pay_creation_days"))
    fast_invoice = int(param(thresholds, "fast_pay_invoice_days"))
    vendor_names = _vendor_names(con)

    new_vendors = con.execute(
        """SELECT account, name, change_date, changed_by, approved_by, source_id
           FROM masterdata_changes
           WHERE field LIKE 'Neuanlage%' AND lower(kind) LIKE 'kredit%'
           ORDER BY change_date"""
    ).fetchall()
    if not new_vendors:
        return []

    profiles = []
    for account, name, created, changed_by, approved_by, md_sid in new_vendors:
        inv_rows = con.execute(
            """SELECT entry_id, posting_date, doc_ref, amount, user_id, source_id
               FROM gl WHERE sub_account = ? AND posting_type = 'Kreditorenrechnung'
               ORDER BY posting_date""", [account]).fetchall()
        pay_rows = con.execute(
            """SELECT entry_id, posting_date, doc_ref, amount, user_id, source_id
               FROM gl WHERE sub_account = ? AND posting_type = 'Zahlung'
               ORDER BY posting_date""", [account]).fetchall()
        n_gr = con.execute(
            "SELECT count(*) FROM goods_receipts WHERE vendor_account = ?",
            [account]).fetchone()[0]

        first_inv = inv_rows[0][1] if inv_rows else None
        first_pay = pay_rows[0][1] if pay_rows else None
        # invoice -> payment lag per doc_ref
        inv_by_ref = {r[2]: r[1] for r in inv_rows if r[2]}
        lags = [(p[1] - inv_by_ref[p[2]]).days for p in pay_rows
                if p[2] in inv_by_ref]
        profiles.append({
            "account": account, "name": name, "created": created,
            "changed_by": changed_by, "approved_by": approved_by,
            "md_sid": md_sid, "inv_rows": inv_rows, "pay_rows": pay_rows,
            "n_goods_receipts": int(n_gr), "first_inv": first_inv,
            "first_pay": first_pay, "pay_lags_days": lags,
        })

    peers_with_evidence = sum(1 for p in profiles if p["n_goods_receipts"] > 0)

    out = []
    for p in profiles:
        if p["first_pay"] is None:
            continue
        days_to_pay = (p["first_pay"] - p["created"]).days
        min_lag = min(p["pay_lags_days"]) if p["pay_lags_days"] else None
        fast = days_to_pay <= fast_create or (min_lag is not None and min_lag <= fast_invoice)
        if not fast:
            continue
        tier = "high" if p["n_goods_receipts"] == 0 else "medium"
        gross_total = round(sum(-r[3] for r in p["inv_rows"]), 2)
        entry_ids = [r[0] for r in p["inv_rows"]] + [r[0] for r in p["pay_rows"]]
        source_ids = [p["md_sid"]] + [r[5] for r in p["inv_rows"]] + [r[5] for r in p["pay_rows"]]
        out.append(_candidate(
            "R06", "new_vendor_fast_pay", "control", "rule_based", tier,
            f"vendor:{p['account']}", f"{p['name']} ({p['account']})",
            entry_ids, source_ids,
            {
                "creation_date": str(p["created"]),
                "created_by": p["changed_by"],
                "approved_by": p["approved_by"],
                "first_invoice_date": str(p["first_inv"]) if p["first_inv"] else None,
                "first_payment_date": str(p["first_pay"]),
                "days_creation_to_first_payment": days_to_pay,
                "invoice_to_payment_days": p["pay_lags_days"],
                "n_invoices": len(p["inv_rows"]),
                "n_payments": len(p["pay_rows"]),
                "gross_invoice_total": gross_total,
                "n_goods_receipts": p["n_goods_receipts"],
            },
            {"population_size": len(profiles), "rule_hits": 0,
             "peers_with_expected_evidence": peers_with_evidence},
            (f"Vendor {p['account']} ({p['name']}), created on {p['created']}, "
             f"received its first payment {days_to_pay} days after creation"
             + (f" and invoices were paid within {min_lag} day(s)" if min_lag is not None else "")
             + f"; {p['n_goods_receipts']} goods receipts are on file for this "
               f"vendor while {peers_with_evidence} of {len(profiles)} newly "
               f"created vendors have goods receipts."),
        ))
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# --------------------------------------------------------------------------
# R7 — Bank-details change followed by payment (timing coupling only)
# --------------------------------------------------------------------------

def rule_r07_bank_change_timing(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    window = int(param(thresholds, "bank_change_pay_window_days"))
    vendor_names = _vendor_names(con)
    changes = con.execute(
        """SELECT change_date, account, name, field, changed_by, approved_by, source_id
           FROM masterdata_changes WHERE lower(field) LIKE '%bank%'"""
    ).fetchall()
    n_pop = con.execute("SELECT count(*) FROM masterdata_changes").fetchone()[0]

    out = []
    for change_date, account, name, field, changed_by, approved_by, sid in changes:
        pays = con.execute(
            """SELECT entry_id, posting_date, amount, doc_ref, user_id, source_id
               FROM gl WHERE sub_account = ? AND posting_type = 'Zahlung'
                 AND posting_date > ? AND posting_date <= ?
               ORDER BY posting_date""",
            [account, change_date, change_date + timedelta(days=window)],
        ).fetchall()
        if not pays:
            # A bank-details change is an approval-relevant event per the audit
            # plan even without subsequent payments in the window — emit a
            # low-tier informational candidate so the event is never silent
            # (blind-spot finding, verification round 2026-07-18). Self-approved
            # changes escalate to medium.
            self_approved = bool(changed_by) and changed_by == approved_by
            out.append(_candidate(
                "R07", "bank_change_no_payment_window", "control", "rule_based",
                "medium" if self_approved else "low",
                f"vendor:{account}",
                f"{name or vendor_names.get(account, account)} ({account})",
                [], [sid],
                {
                    "change_date": str(change_date), "field": field,
                    "changed_by": changed_by, "approved_by": approved_by,
                    "self_approved": self_approved,
                    "window_days": window, "n_payments": 0,
                    "note": ("bank-details master-data change with no payments "
                             "inside the observation window; recorded because "
                             "the audit plan marks bank-data changes as "
                             "approval-relevant"),
                },
                {"population_size": int(n_pop), "rule_hits": 0,
                 "peers_with_expected_evidence": 0},
                (f"A bank-details master-data change for vendor {account} dated "
                 f"{change_date} (changed by {changed_by}, approved by "
                 f"{approved_by}) had no subsequent payments within {window} "
                 f"days; the change itself is an approval-relevant event per "
                 f"the audit planning paper."),
            ))
            continue
        total = round(sum(p[2] for p in pays), 2)
        first_gap = (pays[0][1] - change_date).days
        out.append(_candidate(
            "R07", "bank_change_payment_timing", "control", "rule_based", "medium",
            f"vendor:{account}", f"{name or vendor_names.get(account, account)} ({account})",
            [p[0] for p in pays], [sid] + [p[5] for p in pays],
            {
                "change_date": str(change_date), "field": field,
                "changed_by": changed_by, "approved_by": approved_by,
                "window_days": window, "n_payments": len(pays),
                "payments_total": total, "first_payment_gap_days": first_gap,
                "note": ("timing coupling only; the dossier contains no bank "
                         "account numbers or statements, so a change of payment "
                         "routing cannot be verified"),
            },
            {"population_size": int(n_pop), "rule_hits": 0,
             "peers_with_expected_evidence": 0},
            (f"{len(pays)} payment(s) totalling {_fmt_eur(total)} to vendor "
             f"{account} were posted within {window} days after a bank-details "
             f"master-data change dated {change_date}; the data provided does "
             f"not allow verifying whether the payment routing changed."),
        ))
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# --------------------------------------------------------------------------
# R8 — Entry-signature rarity (Schreyer local anomaly: joint-rare, common marginals)
# --------------------------------------------------------------------------

def rule_r08_signature_rarity(con: duckdb.DuckDBPyConnection, thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    max_support = int(param(thresholds, "signature_max_support"))

    sig_rows = con.execute(
        """
        WITH sig AS (
          SELECT entry_id,
                 string_agg(DISTINCT hb_account || ':' ||
                            CASE WHEN amount >= 0 THEN '+' ELSE '-' END,
                            ',' ORDER BY hb_account || ':' ||
                            CASE WHEN amount >= 0 THEN '+' ELSE '-' END) AS signature
          FROM gl GROUP BY entry_id)
        SELECT signature, count(*) AS support, list(entry_id) AS entries
        FROM sig GROUP BY signature ORDER BY support, signature
        """
    ).fetchall()
    n_entries = sum(r[1] for r in sig_rows)
    n_signatures = len(sig_rows)

    marginals = dict(con.execute(
        "SELECT hb_account, count(DISTINCT entry_id) FROM gl GROUP BY 1").fetchall())
    median_marginal = statistics.median(marginals.values()) if marginals else 0

    out = []
    for signature, support, entries in sig_rows:
        if support > max_support:
            continue
        accounts = sorted({part.split(":")[0] for part in signature.split(",")})
        acc_freq = {a: int(marginals.get(a, 0)) for a in accounts}
        if not all(f >= median_marginal for f in acc_freq.values()):
            continue  # not a LOCAL anomaly — some account is itself rare
        for entry_id in entries:
            lines = con.execute(
                """SELECT line_no, account, amount, text, posting_date, doc_ref,
                          posting_type, user_id, source_id
                   FROM gl WHERE entry_id = ? ORDER BY line_no""",
                [entry_id]).fetchall()
            text = lines[0][3] if lines else ""
            out.append(_candidate(
                "R08", "entry_signature_rarity", "statistical", "local", "low",
                f"entry:{entry_id}", f"Entry {entry_id} ({text})",
                [entry_id], [ln[8] for ln in lines],
                {
                    "signature": signature,
                    "signature_support": int(support),
                    "n_entries_total": int(n_entries),
                    "n_signatures_total": int(n_signatures),
                    "account_marginal_frequencies": acc_freq,
                    "median_account_frequency": float(median_marginal),
                    "posting_date": str(lines[0][4]) if lines else None,
                    "doc_ref": lines[0][5] if lines else None,
                    "posting_type": lines[0][6] if lines else None,
                    "user_id": lines[0][7] if lines else None,
                    "entry_amount_max": max((abs(ln[2]) for ln in lines), default=0.0),
                },
                {"population_size": int(n_entries), "rule_hits": 0,
                 "peers_with_expected_evidence": 0},
                (f"Journal entry {entry_id} ('{text}') uses the account/sign "
                 f"combination {signature} that appears in {support} of "
                 f"{n_entries} entries, although each involved account occurs "
                 f"in at least {int(median_marginal)} entries individually."),
            ))
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

RULES = [
    {"rule_id": "R01", "fn": rule_r01_sod},
    {"rule_id": "R02", "fn": rule_r02_self_approval},
    {"rule_id": "R03", "fn": rule_r03_threshold_splitting},
    {"rule_id": "R04", "fn": rule_r04_cutoff},
    {"rule_id": "R05", "fn": rule_r05_post_lock},
    {"rule_id": "R06", "fn": rule_r06_new_vendor_fast_pay},
    {"rule_id": "R07", "fn": rule_r07_bank_change_timing},
    {"rule_id": "R08", "fn": rule_r08_signature_rarity},
]
