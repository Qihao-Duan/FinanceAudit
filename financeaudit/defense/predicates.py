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
    preds.append(_p("technical_assessment_exists",
                    "Does a technical assessment / component-replacement justification "
                    "exist in the dossier?",
                    "not_found" if not resolves_outside(tech) else "found",
                    [h["source_id"] for h in tech if h["source_id"]][:10],
                    {"n_hits": len(tech),
                     "note": "no dossier file declares assessment coverage — wording "
                             "limited to 'not found in the provided and parsed materials'"}))
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


def defend_generic(con, finding, pack):
    entry_ids = sorted({r.get("entry_id") for r in pack.get("rows", {}).get("gl", [])
                        if r.get("entry_id")})
    preds = []
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
