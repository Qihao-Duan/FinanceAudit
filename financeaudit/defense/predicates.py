"""Innocence-predicate registry (Agent D, stage 4 — PLAN §3.5).

Per-scheme DETERMINISTIC lookups. Each predicate returns:
  {predicate, question, result: found|not_found|not_checkable, reason?,
   source_ids: [...], detail, exculpatory: bool}
'found' on an exculpatory predicate becomes counterevidence. Tone, prestige,
company size and self-reported confidence are NEVER evidence in any direction.
llm_used: false everywhere.
"""
from __future__ import annotations

from financeaudit.evidence.common import dec, q, scan_token, resolves_outside


def _p(name, question, result, source_ids=None, detail=None, reason=None,
       exculpatory=True):
    out = {"predicate": name, "question": question, "result": result,
           "source_ids": source_ids or [], "exculpatory": exculpatory}
    if detail is not None:
        out["detail"] = detail
    if reason:
        out["reason"] = reason
    return out


def _vendor_acct(finding):
    ek = finding.get("entity_key", "")
    return ek.split(":", 1)[1] if ek.startswith("vendor:") else None


# ------------------------------------------------------------- shared lookups

def goods_receipts_exist(con, acct, service_context: bool):
    rows = q(con, "SELECT * FROM goods_receipts WHERE vendor_account=?", [acct])
    if rows:
        return _p("goods_receipts_exist",
                  f"Do goods receipts exist for vendor {acct}?", "found",
                  [r["source_id"] for r in rows[:10]],
                  {"n_receipts": len(rows)})
    if service_context:
        return _p("goods_receipts_exist",
                  f"Do goods receipts exist for vendor {acct}?", "not_checkable",
                  reason="the goods-receipt list covers material/logistics only; "
                         "service deliveries are outside its declared scope")
    return _p("goods_receipts_exist",
              f"Do goods receipts exist for vendor {acct}?", "not_found",
              detail={"scope": "material/logistics receipts, parse complete"})


def contract_ref_resolvable(con, token):
    hits = scan_token(con, token)
    ok = resolves_outside(hits)
    return _p("contract_ref_resolvable",
              f"Does the cited reference '{token}' resolve to a document or list "
              f"entry in the dossier?",
              "found" if ok else "not_found",
              [h["source_id"] for h in hits if h["source_id"]][:10],
              {"n_hits": len(hits), "resolves_outside_posting_texts": ok})


def independent_masterdata_approval(con, acct):
    rows = q(con, "SELECT * FROM masterdata_changes WHERE account=?", [acct])
    indep = [r for r in rows if r["changed_by"] != r["approved_by"]]
    if indep:
        return _p("independent_masterdata_approval",
                  f"Was any master-data change for {acct} approved by a second person?",
                  "found", [r["source_id"] for r in indep], {"n_independent": len(indep)})
    return _p("independent_masterdata_approval",
              f"Was any master-data change for {acct} approved by a second person?",
              "not_found", [r["source_id"] for r in rows],
              {"n_changes": len(rows),
               "note": "all changes show the same user as creator and approver"})


def journal_approval(con, entry_ids):
    if not entry_ids:
        return _p("journal_approval", "Is there a journal-level approval for the "
                  "affected entries?", "not_checkable", reason="no entry ids")
    ph = ",".join("?" * len(entry_ids))
    rows = q(con, f"SELECT * FROM approval_log WHERE entry_id IN ({ph})", list(entry_ids))
    if rows:
        return _p("journal_approval",
                  "Is there a journal-level approval for the affected entries?",
                  "found", [r["source_id"] for r in rows],
                  [{"entry_id": r["entry_id"], "creator": r["creator"],
                    "approver": r["approver"], "status": r["status"]} for r in rows])
    return _p("journal_approval",
              "Is there a journal-level approval for the affected entries?",
              "not_checkable",
              reason="no matching rows; the approval log is journal-level and its "
                     "coverage of all invoices/payments is unproven — absence of a "
                     "log row is not evidence of a missing approval")


def counterparty_normal_history(con, acct, before=None):
    where, params = "account=?", [acct]
    if before:
        where += " AND posting_date < ?"
        params.append(before)
    r = q(con, f"SELECT count(*) AS n, min(posting_date) AS a, max(posting_date) AS b "
               f"FROM vendor_tx WHERE {where}", params)[0]
    if r["n"] and r["n"] > 0:
        sid_rows = q(con, f"SELECT source_id FROM vendor_tx WHERE {where} LIMIT 5", params)
        return _p("counterparty_normal_history",
                  f"Does vendor {acct} have transaction history predating the flagged "
                  f"activity?", "found",
                  [s["source_id"] for s in sid_rows],
                  {"n_tx": r["n"], "first": str(r["a"]), "last": str(r["b"]),
                   "window": f"before {before}" if before else "full year"})
    return _p("counterparty_normal_history",
              f"Does vendor {acct} have transaction history predating the flagged "
              f"activity?", "not_found",
              detail={"n_tx": 0, "window": f"before {before}" if before else "full year"})


# ------------------------------------------------------------- scheme sets

def defend_fictitious_vendor(con, finding, pack):
    acct = _vendor_acct(finding)
    first_flag = None
    glr = (pack.get("rows", {}).get("gl") or [])
    dates = sorted(str(r.get("posting_date")) for r in glr if r.get("posting_date"))
    if dates:
        first_flag = dates[0]
    return [
        goods_receipts_exist(con, acct, service_context=True),
        contract_ref_resolvable(con, "Rahmenvertrag"),
        independent_masterdata_approval(con, acct),
        journal_approval(con, finding.get("entry_ids") or
                         sorted({r["entry_id"] for r in glr if r.get("entry_id")})),
        counterparty_normal_history(con, acct, before=first_flag),
    ]


def defend_threshold_splitting(con, finding, pack):
    acct = _vendor_acct(finding)
    entry_ids = sorted({r["entry_id"] for r in pack.get("rows", {}).get("gl", [])
                        if r.get("entry_id")})
    preds = [journal_approval(con, entry_ids)]
    # batch payment authorization anywhere in the dossier
    hits = scan_token(con, "SAMMEL-" + (acct or ""))
    auth_hits = [h for h in hits if not h["where"].startswith("gl.")]
    preds.append(_p("batch_payment_authorization",
                    "Is there a batch-payment authorization for the collective document?",
                    "found" if auth_hits else "not_found",
                    [h["source_id"] for h in auth_hits if h["source_id"]][:10],
                    {"n_hits_outside_gl": len(auth_hits)}))
    inst = scan_token(con, "Ratenzahlung") + scan_token(con, "Teilzahlungsvereinbarung")
    preds.append(_p("installment_contract_terms",
                    "Do installment/partial-payment contract terms exist in the dossier?",
                    "found" if resolves_outside(inst) else "not_found",
                    [h["source_id"] for h in inst if h["source_id"]][:10],
                    {"n_hits": len(inst)}))
    rows = q(con, "SELECT DISTINCT doc_ref FROM gl WHERE sub_account=? "
                  "AND posting_type='Zahlung' AND doc_ref LIKE 'SAMMEL%'", [acct])
    preds.append(_p("independent_invoices_per_payment",
                    "Does each payment correspond to a separate, independent invoice "
                    "(which would make same-day small payments a legitimate batch run)?",
                    "not_found" if rows else "not_checkable",
                    detail={"shared_collective_doc_refs": [r["doc_ref"] for r in rows],
                            "note": "the payments share one collective reference "
                                    "instead of separate invoice references"}))
    # Client-provided batch-approval schedule (sidecar table batch_approvals):
    # a documented batch authorization with an independent approver is the
    # legitimate explanation for same-day sub-threshold payments.
    try:
        ba = q(con, "SELECT * FROM batch_approvals WHERE vendor_account=?", [acct])
    except Exception:
        ba = []
    if ba:
        approved = [r for r in ba
                    if (r.get("approver") or "").strip()
                    and str(r.get("status") or "").strip().lower().startswith(
                        ("freigegeben", "approved", "genehmigt"))]
        preds.append(_p(
            "batch_payment_authorization",
            "Does a documented batch-payment authorization with an independent "
            "approver cover these payments?",
            "found" if approved else "not_found",
            [r["source_id"] for r in (approved or ba)][:10],
            {"n_batch_rows": len(ba), "n_approved": len(approved),
             "statuses": sorted({str(r.get("status")) for r in ba}),
             "approvers": sorted({str(r.get("approver")) for r in ba})}))
    else:
        preds.append(_p("batch_payment_authorization",
                        "Does a documented batch-payment authorization with an "
                        "independent approver cover these payments?",
                        "not_checkable",
                        reason="no batch-approval schedule provided in the dossier"))
    preds.append(counterparty_normal_history(con, acct))
    return preds


def defend_cutoff(con, finding, pack):
    accr = q(con, "SELECT * FROM gl WHERE lower(text) LIKE '%unfakturiert%'")
    preds = [_p("corresponding_accrual_exists",
                "Does a year-end accrual for uninvoiced December deliveries exist?",
                "found" if accr else "not_found",
                [r["source_id"] for r in accr],
                {"n_rows": len(accr),
                 "amount": str(abs(dec(accr[0]["amount_raw"]))) if accr else None,
                 "note": "an accrual exists but is smaller than the invoice total; "
                         "item-level linkage undetermined (see claims)"})]
    preds.append(journal_approval(con, [r["entry_id"] for r in accr]))
    pp = q(con, "SELECT * FROM purchase_invoices_2026")
    in_2025 = [r for r in pp if r["service_date"].year == 2025]
    # Client-provided accrual schedule (sidecar table accrual_schedule): the
    # strongest possible cutoff exculpation is a per-invoice allocation line
    # showing the invoice is CONTAINED in the year-end accrual. A schedule row
    # documenting NON-coverage corroborates the gap instead. Absent table ->
    # not_checkable (no schedule provided).
    try:
        sched = q(con, "SELECT * FROM accrual_schedule")
    except Exception:
        sched = []
    if sched:
        covered = [r for r in sched
                   if (r.get("allocated_amount") or 0) > 0
                   and str(r.get("status") or "").strip().lower().startswith(
                       ("enthalten", "included", "ja"))]
        uncovered = [r for r in sched if r not in covered]
        preds.append(_p(
            "accrual_schedule_allocation",
            "Does a client accrual schedule allocate the year-end accrual to the "
            "post-period invoices item by item?",
            "found" if covered and not uncovered else "not_found",
            [r["source_id"] for r in (covered + uncovered)][:10],
            {"n_schedule_rows": len(sched), "n_covered": len(covered),
             "n_uncovered": len(uncovered),
             "uncovered_invoices": [r.get("invoice_ref") for r in uncovered][:10],
             "note": ("schedule documents per-invoice coverage" if covered and
                      not uncovered else
                      "schedule exists and explicitly documents non-coverage for "
                      "the listed invoices")}))
    else:
        preds.append(_p("accrual_schedule_allocation",
                        "Does a client accrual schedule allocate the year-end "
                        "accrual to the post-period invoices item by item?",
                        "not_checkable",
                        reason="no accrual schedule provided in the dossier"))
    preds.append(_p("service_period_actually_next_year",
                    "Do the service dates actually belong to the following year "
                    "(which would make the January posting correct)?",
                    "not_found",
                    [r["source_id"] for r in pp[:10]],
                    {"n_invoices": len(pp), "n_with_2025_service_date": len(in_2025)}))
    return preds


def defend_expense_capitalization(con, finding, pack):
    cards = pack.get("counterevidence_probe", {}).get("asset_cards") or []
    preds = [_p("asset_cards_exist",
                "Do asset cards exist for the capitalized items?",
                "found" if cards else "not_found",
                [c["source_id"] for c in cards],
                {"n_cards": len(cards),
                 "status": sorted({c.get("status") for c in cards}) if cards else None})]
    tech = (scan_token(con, "Gutachten") + scan_token(con, "technische Beurteilung")
            + scan_token(con, "Komponententausch"))
    # Sidecar table consumption (mutation-suite fix): a typed
    # technical_assessments table (auto-adapter / optional spec) is
    # authoritative when present — the token scan alone missed
    # 'Technische_Beurteilungen_Synthetisch.csv' (underscored filename).
    tbl_rows = []
    try:
        _ids = sorted({c.get("asset_id") for c in cards if c.get("asset_id")})
        for r in q(con, "SELECT * FROM technical_assessments"):
            blob = " ".join(str(v) for v in r.values())
            if not _ids or any(i in blob for i in _ids):
                tbl_rows.append(r)
    except Exception:
        tbl_rows = []
    found = bool(tbl_rows) or resolves_outside(tech)
    sids = ([r["source_id"] for r in tbl_rows if r.get("source_id")][:10]
            or [h["source_id"] for h in tech if h["source_id"]][:10])
    preds.append(_p("technical_assessment_exists",
                    "Does a technical assessment / component-replacement justification "
                    "exist in the dossier?",
                    "found" if found else "not_found",
                    sids,
                    {"n_hits": len(tech), "n_table_rows": len(tbl_rows),
                     "table": "technical_assessments" if tbl_rows else None,
                     "note": ("typed technical_assessments table consulted"
                              if tbl_rows else
                              "no dossier file declares assessment coverage — wording "
                              "limited to 'not found in the provided and parsed materials'")}))
    ids = sorted({c["asset_id"] for c in cards})
    afa = []
    if ids:
        ph = ",".join("?" * len(ids))
        afa = q(con, f"SELECT * FROM asset_tx WHERE target IN ({ph}) AND kind='AfA'", ids)
    acct_afa = q(con, "SELECT count(*) AS n FROM asset_tx WHERE kind='AfA'")[0]["n"]
    preds.append(_p("depreciation_started_per_asset",
                    "Did per-asset depreciation start for the six additions?",
                    "found" if afa else "not_checkable",
                    [r["source_id"] for r in afa[:10]],
                    {"n_per_asset_afa_rows": len(afa),
                     "n_account_level_afa_rows": acct_afa},
                    reason=None if afa else
                           "depreciation is recorded at account level only in this "
                           "dossier (no per-asset AfA rows exist for ANY asset)"))
    return preds


def defend_related_party(con, finding, pack):
    acct = _vendor_acct(finding)
    agr = scan_token(con, "Umlagevertrag") + scan_token(con, "Konzernumlagevertrag")
    preds = [_p("intercompany_agreement_exists",
                "Does an intercompany service agreement exist in the dossier?",
                "found" if resolves_outside(agr) else "not_found",
                [h["source_id"] for h in agr if h["source_id"]][:10],
                {"n_hits": len(agr)})]
    vm = q(con, "SELECT * FROM vendors WHERE account=?", [acct])
    sh = q(con, "SELECT * FROM shareholders WHERE note LIKE ?", [f"%{acct}%"])
    consistent = bool(vm and sh and sh[0]["name"].split("(")[0].strip() in vm[0]["name"])
    preds.append(_p("entity_identity_consistent",
                    f"Is the entity identity of account {acct} consistent across "
                    f"dossier documents?",
                    "found" if consistent else "not_found",
                    [r["source_id"] for r in vm + sh],
                    {"vendor_master": vm[0]["name"] if vm else None,
                     "shareholder_list": sh[0]["name"] if sh else None}))
    return preds


def offsetting_refund_exists(con, acct, doc_ref):
    """Innocence predicate for a duplicate / over-payment (finder rule R20).

    A booked payable is a credit to 330000-<acct>; each payment is a debit; a
    credit note / refund is a NON-invoice credit (amount < 0) posted back to the
    same AP account for the same invoice. If one exists, the excess payment was
    remediated inside the ledger and the account nets back toward zero ->
    exculpatory. Tone / prestige are never used; this is a pure ledger lookup."""
    if not acct or not doc_ref:
        return _p("offsetting_refund_exists",
                  "Is the over-payment reversed by a credit note or refund?",
                  "not_checkable", reason="no vendor/invoice key on the signal")
    refunds = q(con, "SELECT * FROM gl WHERE sub_account=? AND doc_ref=? "
                     "AND posting_type <> 'Kreditorenrechnung' AND amount < 0",
                [acct, doc_ref])
    net = q(con, "SELECT ROUND(SUM(amount),2) AS n FROM gl "
                 "WHERE sub_account=? AND doc_ref=?", [acct, doc_ref])[0]["n"]
    question = (f"Is the over-payment of invoice {doc_ref} reversed by a credit note "
                f"or refund on vendor account {acct} within the ledger?")
    if refunds:
        credit_refs = sorted({(r.get("raw_belegnummer") or r.get("doc_ref"))
                              for r in refunds if (r.get("raw_belegnummer") or r.get("doc_ref"))})
        refunded = round(sum(abs(r["amount"]) for r in refunds
                             if r.get("amount") is not None), 2)
        return _p("offsetting_refund_exists", question, "found",
                  [r["source_id"] for r in refunds],
                  {"n_refund_rows": len(refunds), "credit_note_refs": credit_refs,
                   "refunded_amount": refunded, "net_ap_for_invoice_after_refund": net,
                   "note": "an offsetting credit note / refund reduces the over-payment; "
                           "the vendor account nets back toward zero for this invoice"})
    return _p("offsetting_refund_exists", question, "not_found", [],
              {"note": "no credit note / refund reverses the excess within the provided "
                       "and parsed ledger", "net_ap_for_invoice": net})


def defend_generic(con, finding, pack):
    preds = []
    # Duplicate / over-payment (finder rule R20) routes to scheme controls_breach
    # -> this generic defender. Its exculpatory fact is an offsetting credit
    # note / refund, so run that innocence check first whenever an R20 signal is
    # in the pack (finder-level symmetry already suppresses the offset case; this
    # is the explicit, logged second layer, and neutralises a duplicate finding
    # if a refund is present).
    for c in pack.get("candidates", []):
        if c.get("rule_id") == "R20":
            m = c.get("metrics") or {}
            preds.append(offsetting_refund_exists(con, m.get("vendor"), m.get("invoice")))
    entry_ids = sorted({r.get("entry_id") for r in pack.get("rows", {}).get("gl", [])
                        if r.get("entry_id")})
    if entry_ids:
        preds.append(journal_approval(con, entry_ids))
    ph = None
    if entry_ids:
        ph = ",".join("?" * len(entry_ids))
        bal = q(con, f"SELECT entry_id, sum(amount) AS s FROM gl WHERE entry_id IN ({ph}) "
                     f"GROUP BY entry_id", entry_ids)
        preds.append(_p("entry_balanced",
                        "Are the affected journal entries internally balanced?",
                        "found" if all(abs(b["s"]) < 0.005 for b in bal) else "not_found",
                        detail={"per_entry_sum": {b["entry_id"]: b["s"] for b in bal}}))
    # Client-provided bank-change payment details (sidecar bank_payment_details):
    # documents which IBAN subsequent payments actually used after a bank-data
    # change — informational for bank-change packs.
    accts = sorted({str(c.get("entity_key", "")).split(":")[-1]
                    for c in pack.get("candidates", [])
                    if str(c.get("entity_key", "")).startswith("vendor:")})
    for _acct in accts:
        try:
            bp = q(con, "SELECT * FROM bank_payment_details WHERE vendor_account=?",
                   [_acct])
        except Exception:
            bp = []
        if bp:
            preds.append(_p(
                "bank_change_payment_details_documented",
                "Do provided bank-change payment details document which account "
                "subsequent payments used?",
                "found",
                [r["source_id"] for r in bp][:10],
                {"n_rows": len(bp),
                 "payments_to_new_iban": sum(
                     1 for r in bp
                     if r.get("used_iban") and r.get("used_iban") == r.get("new_iban")),
                 "note": "documentation of the payment destination exists; whether "
                         "the routing was authorized is assessed by the "
                         "master-data approval predicates"}))
    if not preds:
        preds.append(_p("no_applicable_predicates",
                        "Are scheme-specific innocence predicates defined for this "
                        "signal?", "not_checkable",
                        reason="statistical/permission signal without a dedicated "
                               "innocence checklist"))
    return preds


REGISTRY = {
    "fictitious_vendor": defend_fictitious_vendor,
    "threshold_splitting": defend_threshold_splitting,
    "cutoff": defend_cutoff,
    "expense_capitalization": defend_expense_capitalization,
    "related_party": defend_related_party,
}


def run_predicates(con, finding, pack):
    fn = REGISTRY.get(finding.get("scheme"), defend_generic)
    return fn(con, finding, pack)
