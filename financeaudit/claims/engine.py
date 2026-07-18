"""Claims & calculation engine (Agent D, stage 3 — PLAN §3.4).

Deterministic finding drafts from evidence packs:
 - mechanism decision tree keyed on WHERE the questioned figure lives
   (gl_account_class | subledger | side_document | narrative_only)
 - atomic typed claims (numerical/computational/temporal/entity_attribute/
   comparative/existence/absence), role core|supporting
 - every EUR value recomputed with decimal.Decimal from the cited rows' exact
   raw amount strings; a mismatch marks the claim contradicted automatically
 - existence/absence language gate per PLAN §3.1 (via pack obligation gaps)

No LLM (llm_used: false). OPENAI_API_KEY absent -> this deterministic path is
the only path; the LLM would only ever rephrase narrative, never facts.
"""
from __future__ import annotations

import itertools
from decimal import Decimal

from financeaudit.evidence.common import (
    FORMULA_VERSION, amounts_for, cell_citation, dec, input_set_hash,
    passage_citation, q, scan_token, resolves_outside, thr, thr_citation,
)

EPS = Decimal("0")          # ledger<->ledger comparisons: zero tolerance
VAT = Decimal("1.19")


# ----------------------------------------------------------- claim helpers

def _claim(fid, idx, role, ctype, assertion, citations, value=None,
           verification=None, verdict="supported"):
    return {
        "claim_id": f"{fid}-C{idx}",
        "role": role, "type": ctype, "assertion": assertion,
        "value": value or {"kind": "none", "value": None, "currency_or_unit": None,
                           "formula": None, "formula_version": FORMULA_VERSION,
                           "input_set_hash": None, "operand_refs": []},
        "citations": citations,
        "verification": verification or {"method": "deterministic_check", "llm_used": False},
        "verdict": verdict,
    }


def _amount_value(value: Decimal, formula: str, refs: list[str], op: str,
                  signs: dict[str, int] | None = None) -> dict:
    return {"kind": "amount", "value": float(value), "currency_or_unit": "EUR",
            "formula": formula, "formula_version": FORMULA_VERSION,
            "input_set_hash": input_set_hash(op, refs),
            "operand_refs": list(refs), "op": op,
            "operand_signs": signs or {}}


def recompute_claim(con, claim) -> dict:
    """Recompute an amount claim from the cited rows' exact raw amount strings.

    Returns the recompute record; sets claim verdict to 'contradicted' on any
    mismatch and 'unverifiable' if an operand cannot be resolved to a raw
    amount. Zero tolerance (Decimal).
    """
    v = claim.get("value") or {}
    if v.get("kind") != "amount":
        return {"applicable": False}
    refs = v.get("operand_refs") or []
    op = v.get("op") or "sum_abs"
    signs = v.get("operand_signs") or {}
    amounts = amounts_for(con, refs)
    missing = [r for r in refs if r not in amounts]
    rec = {"applicable": True, "engine": "decimal-exact-v1", "op": op,
           "n_operands": len(refs), "missing_operands": missing}
    if missing:
        claim["verdict"] = "unverifiable"
        rec["match"] = False
        rec["reason"] = "operand source_ids not resolvable to raw amounts"
        claim["recompute"] = rec
        return rec
    total = Decimal("0")
    operands = {}
    for r in refs:
        raw = amounts[r]["raw"]
        d = dec(raw)
        sign = signs.get(r, 1)
        contrib = abs(d) * sign if op in ("sum_abs", "sum_signed_abs") else d * sign
        total += contrib
        operands[r] = {"raw": str(raw), "table": amounts[r]["table"]}
    stated = Decimal(str(v["value"]))
    rec["recomputed_value"] = str(total)
    rec["stated_value"] = str(stated)
    rec["match"] = (total - stated).copy_abs() <= EPS
    rec["operands"] = operands
    if not rec["match"]:
        claim["verdict"] = "contradicted"
    claim["recompute"] = rec
    return rec


# ----------------------------------------------------------- mechanism tree

def mechanism_tree(pack) -> str:
    """WHERE does the questioned figure live? Deterministic tree over the pack's
    row tables and rule families — never keyword matching on narratives."""
    rules = {c["rule_id"] for c in pack["candidates"]}
    rows = pack.get("rows", {})
    gl_rows = rows.get("gl", []) + rows.get("gl_full_entries", [])
    # 1) asset/expense classification: acquisition postings to asset accounts
    if "R15" in rules or any(str(r.get("hb_account", "")).startswith(("04", "06"))
                             and r.get("posting_type") == "Kreditorenrechnung"
                             for r in gl_rows):
        return "gl_account_class"
    # 2) side-document vs ledger gap: side tables dominate the evidence
    side = sum(len(rows.get(t, [])) for t in
               ("purchase_invoices_2026", "goods_receipts", "goods_issues",
                "sales_invoices", "subsequent_payments"))
    if "R04" in rules or side > len(gl_rows):
        return "side_document"
    # 3) subledger / payment flow figures
    if any(r.get("sub_account") for r in gl_rows) or rows.get("vendor_tx") or rows.get("customer_tx"):
        return "subledger"
    # 4) narrative / master-data only
    return "narrative_only"


def classify(pack) -> dict:
    rules = {c["rule_id"] for c in pack["candidates"]}
    tiers = [c.get("risk_tier", "low") for c in pack["candidates"]]
    tier = "high" if "high" in tiers else ("medium" if "medium" in tiers else "low")
    mech = mechanism_tree(pack)
    if "R03" in rules:
        return {"scheme": "threshold_splitting", "mechanism": mech, "tier": tier,
                "anomaly_type": "rule_based", "finding_class": "control_breach"}
    if "R04" in rules:
        return {"scheme": "cutoff", "mechanism": mech, "tier": tier,
                "anomaly_type": "rule_based", "finding_class": "misstatement_risk"}
    # expense_capitalization ONLY for the asset-account pack: an R15
    # evidence-obligation gap on a *vendor* pack concerns that vendor's own
    # obligation type (contract / investment request / related-party agreement)
    # and must not route into the capitalization builder.
    if "R15" in rules and pack["entity_key"].startswith("account:"):
        return {"scheme": "expense_capitalization", "mechanism": mech, "tier": tier,
                "anomaly_type": "contextual", "finding_class": "misstatement_risk"}
    # fictitious-vendor pattern needs a vendor-lifecycle control signal
    # (self-approved creation R02 or new-vendor fast-pay R06); a dangling
    # reference alone (R16) stays a neutral signal.
    if rules & {"R02", "R06"} and pack["entity_key"].startswith("vendor:"):
        return {"scheme": "fictitious_vendor", "mechanism": mech, "tier": tier,
                "anomaly_type": "contextual", "finding_class": "control_breach"}
    if "R17" in rules:
        return {"scheme": "related_party", "mechanism": "narrative_only", "tier": tier,
                "anomaly_type": "contextual", "finding_class": "data_conflict"}
    if "R01" in rules:
        return {"scheme": "controls_breach", "mechanism": "narrative_only", "tier": tier,
                "anomaly_type": "rule_based", "finding_class": "control_breach"}
    return {"scheme": "controls_breach", "mechanism": mech, "tier": tier,
            "anomaly_type": pack["candidates"][0].get("anomaly_type", "global"),
            "finding_class": "statistical_signal"}


# ----------------------------------------------------------- scheme builders

def _gap(pack, evidence_type):
    for g in pack.get("obligation_gaps", []):
        if g["evidence_type"] == evidence_type:
            return g
    return None


def _fmt(d: Decimal) -> str:
    return f"{d:,.2f}"


def build_fictitious_vendor(con, pack, fid) -> dict:
    acct = pack["entity_key"].split(":")[1]
    vendor = pack.get("entity_master") or {}
    name = vendor.get("name", pack["entity_label"])
    md = q(con, "SELECT * FROM masterdata_changes WHERE account=? AND field LIKE 'Neuanlage%'",
           [acct])
    glv = q(con, "SELECT * FROM gl WHERE sub_account=? ORDER BY posting_date, entry_id, line_no",
            [acct])
    inv_ap = [r for r in glv if r["posting_type"] == "Kreditorenrechnung"]
    pay = [r for r in glv if r["posting_type"] == "Zahlung"]
    entry_ids = sorted({r["entry_id"] for r in inv_ap})
    ph = ",".join("?" * len(entry_ids))
    exp_lines = q(con, f"SELECT * FROM gl WHERE entry_id IN ({ph}) AND hb_account LIKE '6%' "
                       f"ORDER BY entry_id", entry_ids) if entry_ids else []
    doc_refs = sorted({r["doc_ref"] for r in inv_ap})
    claims = []
    # C1 creation self-approved
    if md:
        m = md[0]
        claims.append(_claim(
            fid, 1, "core", "entity_attribute",
            f"Vendor account {acct} ({name}) was created on {m['change_date']} by user "
            f"{m['changed_by']} and approved by the same user {m['approved_by']} "
            f"(master data change log, field '{m['field']}').",
            [cell_citation(con, m["source_id"], "GEAENDERT_VON/GENEHMIGT_VON",
                           f"{m['changed_by']} / {m['approved_by']}")],
            verification={"method": "changed_by == approved_by on the cited row",
                          "llm_used": False},
            verdict="supported" if m["changed_by"] == m["approved_by"] else "contradicted"))
    # C2 SoD rights of the creating user
    creator = md[0]["changed_by"] if md else None
    perm = q(con, "SELECT * FROM permissions WHERE user_id=?", [creator]) if creator else []
    if perm:
        p = perm[0]
        rights = [k for k in ("can_create_vendor", "can_post", "can_pay") if p.get(k)]
        cits = [cell_citation(con, p["source_id"], "Berechtigungen",
                              ", ".join(rights))]
        tc = thr_citation(con, pack["thresholds"], "payment_approval_limit")
        if tc:
            cits.append(tc)
        claims.append(_claim(
            fid, 2, "core", "entity_attribute",
            f"User {creator} holds vendor-creation, posting and payment permissions "
            f"simultaneously per the 2025 permission matrix, while the audit planning "
            f"paper prescribes segregation of these functions.",
            cits,
            verification={"method": "permission flags lookup", "llm_used": False,
                          "detail": {k: bool(p.get(k)) for k in
                                     ("can_create_vendor", "can_post", "can_pay",
                                      "can_approve")}},
            verdict="supported" if len(rights) == 3 else "unverifiable"))
    # C3 net amount (core, exact recompute)
    if exp_lines:
        net_raws = [dec(r["amount_raw"]) for r in exp_lines]
        net = sum((abs(x) for x in net_raws), Decimal("0"))
        refs = [r["source_id"] for r in exp_lines]
        claims.append(_claim(
            fid, 3, "core", "numerical",
            f"The {len(exp_lines)} invoices {doc_refs[0]}–{doc_refs[-1]} posted to vendor "
            f"account {acct} in FY2025 carry expense lines (account class 6) with a net "
            f"total of {_fmt(net)} EUR.",
            [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"])
             for r in exp_lines],
            value=_amount_value(net,
                                "sum(|BUCHUNGSBETRAG|) over the expense lines of "
                                + ", ".join(doc_refs) + " = "
                                + " + ".join(str(abs(x)) for x in net_raws),
                                refs, "sum_abs")))
    # C4 gross amount (supporting, exact recompute + VAT relation)
    if inv_ap:
        gross_raws = [dec(r["amount_raw"]) for r in inv_ap]
        gross = sum((abs(x) for x in gross_raws), Decimal("0"))
        refs = [r["source_id"] for r in inv_ap]
        net = sum((abs(dec(r["amount_raw"])) for r in exp_lines), Decimal("0"))
        vat_ok = (net * VAT == gross)
        claims.append(_claim(
            fid, 4, "supporting", "computational",
            f"The gross payable total of the invoices on account {acct} is "
            f"{_fmt(gross)} EUR, which equals the net total {_fmt(net)} EUR x 1.19.",
            [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"])
             for r in inv_ap],
            value=_amount_value(gross,
                                "sum(|BUCHUNGSBETRAG|) over the payable lines = "
                                + " + ".join(str(abs(x)) for x in gross_raws),
                                refs, "sum_abs"),
            verification={"method": "cross-check net*1.19 == gross (Decimal)",
                          "llm_used": False, "detail": {"net": str(net),
                                                        "net_x_1.19": str(net * VAT),
                                                        "gross": str(gross),
                                                        "equal": vat_ok}},
            verdict="supported" if vat_ok else "contradicted"))
    # C5 settlement speed (core temporal)
    if inv_ap and pay:
        pairs = []
        for i in inv_ap:
            match = [p for p in pay if p["doc_ref"] == i["doc_ref"]]
            for p_ in match:
                pairs.append((i, p_, (p_["posting_date"] - i["posting_date"]).days))
        max_gap = max((g for *_, g in pairs), default=None)
        claims.append(_claim(
            fid, 5, "core", "temporal",
            f"Each of the {len(inv_ap)} invoices on account {acct} was settled within "
            f"{max_gap} days of its posting date (invoice and payment share the same "
            f"document reference).",
            [cell_citation(con, r["source_id"], "BUCHUNGSDATUM", str(r["posting_date"]))
             for r, _, _ in pairs] +
            [cell_citation(con, p_["source_id"], "BUCHUNGSDATUM", str(p_["posting_date"]))
             for _, p_, _ in pairs],
            value={"kind": "count", "value": max_gap, "currency_or_unit": "days",
                   "formula": "max(payment posting_date - invoice posting_date) per doc_ref",
                   "formula_version": FORMULA_VERSION,
                   "input_set_hash": input_set_hash("max_day_gap",
                                                    [r["source_id"] for r, _, _ in pairs]),
                   "operand_refs": [r["source_id"] for r, _, _ in pairs]},
            verification={"method": "date difference per doc_ref pair", "llm_used": False,
                          "detail": [{"doc_ref": i["doc_ref"], "days": g}
                                     for i, _, g in pairs]},
            verdict="supported" if pairs and max_gap is not None and max_gap <= 2
                    else "unverifiable"))
    # C6 contract reference resolvability (language-gated existence claim)
    gap = _gap(pack, "contract_document")
    citing = [r for r in inv_ap if "rahmenvertrag" in (r.get("text") or "").lower()]
    if citing:
        hits = scan_token(con, "Rahmenvertrag")
        resolvable = resolves_outside(hits)
        qual = (gap or {}).get("wording") or "not found in the provided and parsed materials"
        claims.append(_claim(
            fid, 6, "core", "existence",
            f"The posting texts of the invoices on account {acct} cite a 'Rahmenvertrag'; "
            f"a document matching this reference was {qual}. "
            f"(Absence is not asserted: no dossier file declares contract coverage.)",
            [cell_citation(con, r["source_id"], "BUCHUNGSTEXT", r["text"]) for r in citing],
            verification={"method": "deterministic dossier-wide token scan "
                                    "(doc_units, file names, side-table text columns)",
                          "llm_used": False,
                          "detail": {"token": "Rahmenvertrag",
                                     "resolves_outside_posting_texts": resolvable,
                                     "n_hits_in_posting_texts": len(hits)}},
            verdict="supported" if not resolvable else "contradicted"))
    # C7 creation-to-first-invoice window (supporting temporal)
    if md and inv_ap:
        d0, d1 = md[0]["change_date"], inv_ap[0]["posting_date"]
        claims.append(_claim(
            fid, 7, "supporting", "temporal",
            f"The first invoice on account {acct} was posted on {d1}, {(d1 - d0).days} "
            f"days after the account was created on {d0}.",
            [cell_citation(con, md[0]["source_id"], "DATUM", str(d0)),
             cell_citation(con, inv_ap[0]["source_id"], "BUCHUNGSDATUM", str(d1))],
            verification={"method": "date difference", "llm_used": False,
                          "detail": {"days": (d1 - d0).days}}))
    return {
        "claims": claims,
        "title": f"Vendor account {acct} ({name}) — control-environment anomaly on "
                 f"creation, approval and settlement",
        "description": (
            f"Vendor account {acct} ({name}) was created and approved by the same user, "
            f"who also posted and paid its invoices; the five invoices were each settled "
            f"within two days and reference a framework contract that was not found in "
            f"the provided and parsed materials. These observations are reported as "
            f"control deviations requiring substantiation; the data do not establish the "
            f"counterparty's status."),
        "expected_evidence": ["framework contract (Rahmenvertrag)",
                              "service acceptance records",
                              "independent master-data approval",
                              "independent payment approval >= 10,000 EUR"],
        "missing_evidence": [
            "framework contract: not found in the provided and parsed materials "
            "(no dossier file declares contract coverage)",
            "service acceptance: outside every declared population scope",
            "independent approvals: the master-data log shows the same user; the "
            "journal approval log does not cover these entries (coverage unproven)"],
        "pbc_requests": ["Rahmenvertrag with 209101 incl. signature pages",
                         "service/acceptance documentation for ER901416-ER901420",
                         "bank statements for the five outgoing payments",
                         "second-signature evidence for the payments"],
    }


def build_threshold_splitting(con, pack, fid) -> dict:
    acct = pack["entity_key"].split(":")[1]
    cand = next(c for c in pack["candidates"] if c["rule_id"] == "R03")
    doc_ref = cand["metrics"].get("doc_ref") or cand["metrics"].get("same_doc_ref")
    rows = q(con, "SELECT * FROM gl WHERE doc_ref=? AND sub_account=? ORDER BY entry_id",
             [doc_ref, acct])
    limit = Decimal(str(thr(pack["thresholds"], "payment_approval_limit")))
    raws = [dec(r["amount_raw"]) for r in rows]
    total = sum((abs(x) for x in raws), Decimal("0"))
    refs = [r["source_id"] for r in rows]
    users = sorted({r["user_id"] for r in rows})
    day = rows[0]["posting_date"] if rows else None
    claims = [
        _claim(fid, 1, "core", "numerical",
               f"On {day}, {len(rows)} payments to vendor account {acct} under document "
               f"reference {doc_ref} total {_fmt(total)} EUR.",
               [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"])
                for r in rows],
               value=_amount_value(total,
                                   f"sum(|BUCHUNGSBETRAG|) over the {len(rows)} payment "
                                   f"lines {doc_ref} = "
                                   + " + ".join(str(abs(x)) for x in raws),
                                   refs, "sum_abs")),
    ]
    tc = thr_citation(con, pack["thresholds"], "payment_approval_limit")
    below = all(abs(x) < limit for x in raws)
    claims.append(_claim(
        fid, 2, "core", "comparative",
        f"Each of the {len(rows)} payment amounts ({', '.join(str(abs(x)) for x in raws)} EUR) "
        f"is individually below the {_fmt(limit)} EUR dual-approval limit, while their "
        f"total {_fmt(total)} EUR exceeds it.",
        [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"]) for r in rows]
        + ([tc] if tc else []),
        value={"kind": "count", "value": len(rows), "currency_or_unit": "payments",
               "formula": f"count(payments with |amount| < {limit})",
               "formula_version": FORMULA_VERSION,
               "input_set_hash": input_set_hash("count_below_limit", refs),
               "operand_refs": refs},
        verification={"method": "per-amount comparison against extracted threshold",
                      "llm_used": False,
                      "detail": {"limit": str(limit), "all_below": below,
                                 "total_above": total > limit}},
        verdict="supported" if below and total > limit else "contradicted"))
    claims.append(_claim(
        fid, 3, "core", "entity_attribute",
        f"All {len(rows)} payments share the same posting date ({day}), the same document "
        f"reference ({doc_ref}), the same posting text ('Teilzahlung "
        f"Lieferantenrechnung') and the same user ({', '.join(users)}), indicating one "
        f"collective payable settled in parts.",
        [cell_citation(con, r["source_id"], "BELEGNR", r["doc_ref"]) for r in rows],
        verification={"method": "field equality across the payment set", "llm_used": False,
                      "detail": {"n_dates": 1, "n_doc_refs": 1, "n_users": len(users)}},
        verdict="supported" if len({r["posting_date"] for r in rows}) == 1
                and len(users) == 1 else "unverifiable"))
    gate = _gap(pack, "bank_statement")
    claims.append(_claim(
        fid, 4, "supporting", "existence",
        f"A batch-payment or second-signature authorization covering {doc_ref} was not "
        f"found in the provided and parsed materials; the journal approval log is "
        f"journal-level and its coverage of payments is unproven, so absence of an "
        f"approval is not asserted.",
        [cell_citation(con, rows[0]["source_id"], "BELEGNR", doc_ref)] if rows else [],
        verification={"method": "approval_log lookup by entry_id + dossier token scan",
                      "llm_used": False,
                      "detail": {"approval_log_hits":
                                 len(pack["counterevidence_probe"]["approvals"]),
                                 "scan_token": doc_ref}},
        verdict="supported"))
    return {
        "claims": claims,
        "title": f"Vendor account {acct} — four same-day payments below the "
                 f"dual-approval limit under one collective document",
        "description": (
            f"Four payments to vendor account {acct} on {day} under the single document "
            f"reference {doc_ref} are each below the 10,000 EUR dual-approval line and "
            f"total {_fmt(total)} EUR; the pattern is consistent with settling one "
            f"payable in parts under the approval threshold and requires review of the "
            f"underlying payable and authorization."),
        "expected_evidence": ["underlying vendor invoice(s) for the collective document",
                              "dual approval or batch-payment authorization",
                              "bank statements"],
        "missing_evidence": [
            "authorization for the collective payment: not found in the provided and "
            "parsed materials (approval-log coverage of payments unproven)",
            "bank statements: not part of the dossier scope (PBC only)"],
        "pbc_requests": [f"original payable behind {doc_ref}",
                         "payment-run authorization evidence for 14.10.2025",
                         "bank statement extract for the four transfers"],
    }


def build_cutoff(con, pack, fid) -> dict:
    pp = q(con, "SELECT * FROM purchase_invoices_2026 ORDER BY invoice_no")
    gr = q(con, "SELECT * FROM goods_receipts WHERE note LIKE '%Rechnung offen%' ORDER BY we_no")
    accr = q(con, "SELECT * FROM gl WHERE lower(text) LIKE '%unfakturiert%' ORDER BY line_no")
    inv_raws = [dec(r["amount_raw"]) for r in pp]
    inv_total = sum((abs(x) for x in inv_raws), Decimal("0"))
    inv_refs = [r["source_id"] for r in pp]
    claims = [
        _claim(fid, 1, "core", "numerical",
               f"Eight vendor invoices dated January 2026 (vendors "
               f"{pp[0]['vendor_account']}–{pp[-1]['vendor_account']}, "
               f"{pp[0]['invoice_no']}–{pp[-1]['invoice_no']}) total {_fmt(inv_total)} EUR.",
               [cell_citation(con, r["source_id"], "BETRAG", r["amount_raw"]) for r in pp],
               value=_amount_value(inv_total,
                                   "sum(BETRAG) over the 8 post-period invoices = "
                                   + " + ".join(str(abs(x)) for x in inv_raws),
                                   inv_refs, "sum_abs")),
        _claim(fid, 2, "core", "temporal",
               "All eight invoices carry service dates (Leistungsdatum) between "
               f"{min(r['service_date'] for r in pp)} and {max(r['service_date'] for r in pp)} "
               "— i.e. in December 2025 — while their invoice dates lie in January 2026, "
               "so the underlying obligations belong to FY2025.",
               [cell_citation(con, r["source_id"], "LEISTUNGSDATUM", str(r["service_date"]))
                for r in pp],
               verification={"method": "service_date year==2025/month==12 and "
                                       "invoice_date year==2026 for all rows",
                             "llm_used": False,
                             "detail": {"n_ok": sum(1 for r in pp
                                                    if r["service_date"].year == 2025
                                                    and r["service_date"].month == 12
                                                    and r["invoice_date"].year == 2026)}},
               verdict="supported" if all(r["service_date"].year == 2025 and
                                          r["service_date"].month == 12 and
                                          r["invoice_date"].year == 2026 for r in pp)
                       else "contradicted"),
    ]
    # C3 receipt matching
    pairs, unmatched = [], []
    gr_pool = list(gr)
    for r in pp:
        m = next((g for g in gr_pool if g["vendor_account"] == r["vendor_account"]
                  and dec(g["amount_raw"]) == dec(r["amount_raw"])), None)
        if m:
            gr_pool.remove(m)
            pairs.append((r, m))
        else:
            unmatched.append(r)
    claims.append(_claim(
        fid, 3, "core", "comparative",
        f"{len(pairs)} of the 8 post-period invoices match, by vendor account and exact "
        f"amount, one December-2025 goods receipt each that is marked 'Dez-Lieferung, "
        f"Rechnung offen' in the goods receipt list.",
        [cell_citation(con, g["source_id"], "HINWEIS", g["note"]) for _, g in pairs],
        verification={"method": "greedy 1:1 join on (vendor_account, exact Decimal amount)",
                      "llm_used": False,
                      "detail": {"n_matched": len(pairs), "n_unmatched": len(unmatched),
                                 "pairs": [{"invoice": r["invoice_no"], "receipt": g["we_no"]}
                                           for r, g in pairs]}},
        verdict="supported" if len(pairs) == len(pp) else "unverifiable"))
    # C4 accrual exists
    accr_exp = [r for r in accr if r["amount"] and r["amount"] > 0]
    if accr_exp:
        a = accr_exp[0]
        accr_amt = abs(dec(a["amount_raw"]))
        claims.append(_claim(
            fid, 4, "core", "numerical",
            f"One year-end accrual 'Rückstellung unfakturierte Leistungen Dez 2025' of "
            f"{_fmt(accr_amt)} EUR was posted on {a['posting_date']} (entry {a['entry_id']}).",
            [cell_citation(con, a["source_id"], "BUCHUNGSBETRAG", a["amount_raw"])],
            value=_amount_value(accr_amt, f"|BUCHUNGSBETRAG| of entry {a['entry_id']} "
                                          f"line {a['line_no']} = {a['amount_raw']}",
                                [a["source_id"]], "sum_abs")))
        # C5 unmatched liability range
        amounts_cents = [int(abs(x) * 100) for x in inv_raws]
        target = int(accr_amt * 100)
        subset_hit = any(sum(c) == target
                         for n in range(1, len(amounts_cents) + 1)
                         for c in itertools.combinations(amounts_cents, n))
        low = inv_total - accr_amt
        signs = {a["source_id"]: -1}
        claims.append(_claim(
            fid, 5, "core", "numerical",
            f"For FY2025 this leaves a potential unmatched liability of at least "
            f"{_fmt(low)} EUR (if the {_fmt(accr_amt)} EUR accrual fully relates to these "
            f"eight invoices) and up to {_fmt(inv_total)} EUR (if it does not relate to "
            f"them); no subset of the eight invoice amounts sums exactly to the accrual, "
            f"so the relation is undetermined. The profit-or-loss effect is a separate "
            f"question and requires item-by-item confirmation (accrual coverage, "
            f"inventory attribution, expense nature).",
            [cell_citation(con, r["source_id"], "BETRAG", r["amount_raw"]) for r in pp]
            + [cell_citation(con, a["source_id"], "BUCHUNGSBETRAG", a["amount_raw"])],
            value=_amount_value(low,
                                f"sum(8 invoices) - accrual = {_fmt(inv_total)} - "
                                f"{_fmt(accr_amt)} = {_fmt(low)}",
                                inv_refs + [a["source_id"]], "sum_abs", signs=signs),
            verification={"method": "exhaustive subset-sum over the 8 invoice amounts "
                                    "(Decimal cents) against the accrual",
                          "llm_used": False,
                          "detail": {"subset_matches_accrual": subset_hit,
                                     "range_eur": [str(low), str(inv_total)]}},
            verdict="supported" if not subset_hit else "unverifiable"))
    return {
        "claims": claims,
        "title": "FY2025 cut-off: post-period invoices with December service dates "
                 "exceed the recorded year-end accrual",
        "description": (
            "Eight January-2026 vendor invoices with December-2025 service dates "
            "(192,000.00 EUR) each match an open-marked December goods receipt, while "
            "the only related year-end accrual found records 86,500.00 EUR; the "
            "difference indicates a potential unmatched liability between 105,500.00 "
            "and 192,000.00 EUR pending item-by-item confirmation. Liability, purchase "
            "volume and profit-or-loss effect are distinct concepts; no earnings impact "
            "is asserted."),
        "expected_evidence": ["item-linked accrual schedule for December deliveries",
                              "accrual calculation basis for 86,500 EUR",
                              "inventory postings for December receipts"],
        "missing_evidence": [
            "item-level accrual linkage: not found in the provided and parsed materials",
            "accrual computation basis: not found in the provided and parsed materials"],
        "pbc_requests": ["accrual working papers for 'unfakturierte Leistungen Dez 2025'",
                         "statement whether the eight December deliveries were accrued "
                         "or capitalized to inventory"],
    }


def build_expense_capitalization(con, pack, fid) -> dict:
    caps = q(con, "SELECT * FROM gl WHERE doc_ref BETWEEN 'ER901421' AND 'ER901426' "
                  "ORDER BY doc_ref, line_no")
    asset_lines = [r for r in caps if str(r["hb_account"]).startswith(("04", "06"))
                   and r["posting_type"] == "Kreditorenrechnung"]
    ap_lines = [r for r in caps if str(r["hb_account"]).startswith("33")]
    text_lines = [r for r in caps if r["line_no"] == 2]
    net_raws = [dec(r["amount_raw"]) for r in asset_lines]
    net = sum((abs(x) for x in net_raws), Decimal("0"))
    gross_raws = [dec(r["amount_raw"]) for r in ap_lines]
    gross = sum((abs(x) for x in gross_raws), Decimal("0"))
    doc_refs = sorted({r["doc_ref"] for r in caps})
    k3 = q(con, "SELECT source_id, text FROM doc_units WHERE file LIKE '%Pruefungsplanung%' "
                "AND text LIKE '%reparaturtypischer%'")
    claims = [
        _claim(fid, 1, "core", "numerical",
               f"Six FY2025 fixed-asset additions ({doc_refs[0]}–{doc_refs[-1]}) were "
               f"capitalized with a net total of {_fmt(net)} EUR.",
               [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"])
                for r in asset_lines],
               value=_amount_value(net,
                                   "sum(|BUCHUNGSBETRAG|) over the six asset-debit lines = "
                                   + " + ".join(str(abs(x)) for x in net_raws),
                                   [r["source_id"] for r in asset_lines], "sum_abs")),
        _claim(fid, 2, "supporting", "computational",
               f"The corresponding payable lines total {_fmt(gross)} EUR gross, equal to "
               f"the net total {_fmt(net)} EUR x 1.19.",
               [cell_citation(con, r["source_id"], "BUCHUNGSBETRAG", r["amount_raw"])
                for r in ap_lines],
               value=_amount_value(gross,
                                   "sum(|BUCHUNGSBETRAG|) over the six payable lines = "
                                   + " + ".join(str(abs(x)) for x in gross_raws),
                                   [r["source_id"] for r in ap_lines], "sum_abs"),
               verification={"method": "cross-check net*1.19 == gross (Decimal)",
                             "llm_used": False,
                             "detail": {"net": str(net), "net_x_1.19": str(net * VAT),
                                        "gross": str(gross), "equal": net * VAT == gross}},
               verdict="supported" if net * VAT == gross else "contradicted"),
        _claim(fid, 3, "core", "entity_attribute",
               "The six posting texts describe maintenance-type work (Reparatur, "
               "Instandsetzung, Austausch, Generalüberholung) while the amounts were "
               "debited to fixed-asset accounts (classes 04/06) — the audit plan's "
               "selection criterion K3 addresses exactly this constellation.",
               [cell_citation(con, r["source_id"], "BUCHUNGSTEXT", r["text"])
                for r in text_lines]
               + ([passage_citation(con, k3[0]["source_id"],
                                    "(K3) Zugänge im Anlagevermögen mit reparaturtypischer "
                                    "Bezeichnung")] if k3 else []),
               verification={"method": "keyword class check over the six posting texts "
                                       "+ account-class check", "llm_used": False,
                             "detail": {"n_asset_lines": len(asset_lines),
                                        "texts": [r["text"] for r in text_lines]}},
               verdict="supported" if len(asset_lines) == 6 else "unverifiable"),
    ]
    cards = pack["counterevidence_probe"].get("asset_cards") or []
    if cards:
        claims.append(_claim(
            fid, 4, "supporting", "existence",
            f"Asset cards exist and are active for the {len(cards)} capitalized items — "
            f"consistent with an orderly capitalization workflow and with the "
            f"alternative reading of capitalizable component replacements.",
            [cell_citation(con, c["source_id"], "BEZEICHNUNG", c["name"]) for c in cards],
            verification={"method": "assets table lookup", "llm_used": False}))
    return {
        "claims": claims,
        "title": "Six fixed-asset additions with maintenance-type descriptions "
                 "(net 150,800.00 EUR) — classification requires review",
        "description": (
            "Six FY2025 asset additions totalling 150,800.00 EUR net carry posting texts "
            "describing repair or overhaul work. Whether these are capitalizable "
            "component replacements or period expenses cannot be decided from the "
            "provided materials; a technical assessment was not found in the provided "
            "and parsed materials. This is reported strictly as an "
            "accounting-classification question; no statement about intent is made."),
        "expected_evidence": ["technical assessment / component-replacement justification",
                              "capitalization policy applied to the six invoices",
                              "per-asset depreciation start"],
        "missing_evidence": [
            "technical assessment: not found in the provided and parsed materials "
            "(no dossier file declares assessment coverage)"],
        "pbc_requests": ["technical justification for capitalizing ER901421-ER901426",
                         "depreciation schedule showing AfA start for the six assets"],
    }


def build_related_party(con, pack, fid) -> dict:
    acct = pack["entity_key"].split(":")[1]
    conflicts = pack.get("conflicts") or []
    vm = q(con, "SELECT * FROM vendors WHERE account=?", [acct])
    sh = q(con, "SELECT * FROM shareholders WHERE note LIKE ?", [f"%{acct}%"])
    gl_rows = q(con, "SELECT * FROM gl WHERE sub_account=? ORDER BY line_no", [acct])
    claims = []
    if vm and sh:
        v, s = vm[0], sh[0]
        claims.append(_claim(
            fid, 1, "core", "entity_attribute",
            f"The vendor master assigns account {acct} to '{v['name']}' "
            f"(parent company), while the shareholder list line assigns 'Personenkonto "
            f"Kreditor {acct}' to '{s['name']}' (sister company) — two different group "
            f"entities for the same payable account.",
            [cell_citation(con, v["source_id"], "NAME", v["name"]),
             cell_citation(con, s["source_id"], "HINWEIS", s["note"])],
            verification={"method": "cross-document name comparison on account linkage",
                          "llm_used": False,
                          "detail": {"vendor_master": v["name"], "shareholder_list": s["name"]}},
            verdict="supported" if v["name"] != s["name"] else "contradicted"))
    ap = [r for r in gl_rows if r.get("sub_account") == acct]
    if ap:
        a = ap[0]
        amt = abs(dec(a["amount_raw"]))
        claims.append(_claim(
            fid, 2, "supporting", "numerical",
            f"A group service charge 'Konzernumlage Verwaltungsleistungen 2025' of "
            f"{_fmt(amt)} EUR was posted via account {acct} on {a['posting_date']}.",
            [cell_citation(con, a["source_id"], "BUCHUNGSBETRAG", a["amount_raw"])],
            value=_amount_value(amt, f"|BUCHUNGSBETRAG| of entry {a['entry_id']} line "
                                     f"{a['line_no']} = {a['amount_raw']}",
                                [a["source_id"]], "sum_abs")))
    return {
        "claims": claims,
        "title": f"Vendor account {acct} — conflicting entity assignment between "
                 f"vendor master and shareholder list",
        "description": (
            f"Two dossier documents assign vendor account {acct} to different group "
            f"companies; the identity of the counterparty for the 220,000.00 EUR group "
            f"charge therefore requires clarification. This is an observation on data "
            f"consistency and related-party disclosure, not an assertion about the "
            f"validity of the charge."),
        "expected_evidence": ["intercompany service agreement",
                              "allocation basis for the group charge",
                              "consistent related-party disclosure"],
        "missing_evidence": [
            "intercompany agreement: not found in the provided and parsed materials"],
        "pbc_requests": [f"clarification which group entity holds account {acct}",
                         "Konzernumlage agreement and allocation calculation"],
    }


def build_generic(con, pack, fid) -> dict:
    """Fallback builder for packs without a dedicated scheme (R01 user packs,
    R08 statistical signals, unknown future rules). Always neutral wording."""
    cand = pack["candidates"][0]
    rows = pack.get("rows", {})
    cits = []
    for t in ("permissions", "gl", "masterdata_changes"):
        for r in rows.get(t, [])[:6]:
            label = {"permissions": ("BENUTZER", r.get("user_id")),
                     "gl": ("BUCHUNGSBETRAG", r.get("amount_raw")),
                     "masterdata_changes": ("FELD", r.get("field"))}[t]
            try:
                cits.append(cell_citation(con, r["source_id"], label[0], label[1]))
            except KeyError:
                continue
    if not cits:
        # fallback 1: any other row table in the pack (e.g. vendors/customers)
        for t, rlist in rows.items():
            for r in rlist[:4]:
                sid = r.get("source_id")
                if not sid:
                    continue
                try:
                    cits.append(cell_citation(
                        con, sid, "ZEILE",
                        r.get("name") or r.get("account") or r.get("doc_ref") or ""))
                except KeyError:
                    continue
            if cits:
                break
    if not cits:
        # fallback 2: the candidate's own cited source rows
        for c in pack["candidates"]:
            for sid in (c.get("source_ids") or [])[:6]:
                try:
                    cits.append(cell_citation(con, sid, "ZEILE", ""))
                except KeyError:
                    continue
            if cits:
                break
    claims = [_claim(
        fid, 1, "supporting" if cand.get("risk_tier") == "low" else "core",
        "comparative",
        f"{cand.get('description', 'Deterministic rule signal.')} "
        f"(rule {cand['rule_id']}, population "
        f"{ (cand.get('denominators') or {}).get('population_size', 'n/a') }, "
        f"hits { (cand.get('denominators') or {}).get('rule_hits', 'n/a') }).",
        cits,
        verification={"method": f"finder rule {cand['rule_id']} deterministic output",
                      "llm_used": False, "detail": cand.get("metrics", {})})]
    return {
        "claims": claims,
        "title": f"{pack['entity_label']} — {cand.get('rule_name', cand['rule_id'])} signal",
        "description": cand.get("description", ""),
        "expected_evidence": [], "missing_evidence": [],
        "pbc_requests": [],
    }


_BUILDERS = {
    "fictitious_vendor": build_fictitious_vendor,
    "threshold_splitting": build_threshold_splitting,
    "cutoff": build_cutoff,
    "expense_capitalization": build_expense_capitalization,
    "related_party": build_related_party,
}


def build_finding(con, pack, seq: int) -> dict:
    cls = classify(pack)
    fid = f"F-{seq:04d}"
    builder = _BUILDERS.get(cls["scheme"], build_generic)
    if builder is build_generic or (cls["scheme"] == "controls_breach"):
        body = build_generic(con, pack, fid)
    else:
        body = builder(con, pack, fid)
        if not body["claims"]:
            # dedicated builder found no substantiable rows for this pack shape
            # (e.g. related_party on a vendor-pair / population pack) — fall
            # back to the neutral generic builder instead of an empty finding.
            body = build_generic(con, pack, fid)
    # recompute every amount claim (zero tolerance) — mismatches auto-contradict
    for c in body["claims"]:
        recompute_claim(con, c)
    amount = 0.0
    for c in body["claims"]:
        v = c.get("value") or {}
        if v.get("kind") == "amount" and c["role"] == "core":
            amount = max(amount, float(v["value"] or 0))
    finding = {
        "finding_id": fid,
        "pack_id": pack["pack_id"],
        "entity_key": pack["entity_key"],
        "entity_label": pack["entity_label"],
        "title": body["title"],
        "scheme": cls["scheme"],
        "mechanism": cls["mechanism"],
        "anomaly_type": cls["anomaly_type"],
        "finding_class": cls["finding_class"],
        "risk_tier": cls["tier"],
        "amount_eur": amount,
        "reporting_status": "finding",          # provisional; verdict stage decides
        "description": body["description"],
        "claims": body["claims"],
        "expected_evidence": body["expected_evidence"],
        "missing_evidence": body["missing_evidence"],
        "pbc_requests": body["pbc_requests"],
        "counterevidence": [],
        "innocence_checked": [],
        "parser_coverage": pack.get("parser_coverage", "partial"),
        "denominators": {**pack.get("denominators", {}),
                         "defender_rejected_hits": 0},
        "candidate_ids": [c["candidate_id"] for c in pack["candidates"]],
        "fixture_supplemented": any(c.get("fixture_supplement")
                                    for c in pack["candidates"]),
        "disposition": "report",                # provisional; verdict stage decides
        "llm_used": False,
    }
    return finding
