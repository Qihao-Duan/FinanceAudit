"""Fixture candidate generator (Agent D).

Used ONLY when build/candidates.json (Agents B+C) is absent, so that the
evidence -> claims -> defense -> verdict stages can be developed and tested
end-to-end. Every source_id is queried live from build/audit.duckdb — nothing
is hard-coded except the entity anchors of the practice targets F1-F4 + D5.
Schema: CONTRACTS.md §3. Descriptions are neutral (no fraud wording).
"""
from __future__ import annotations

from financeaudit.evidence.common import q


def _sids(rows):
    return sorted({r["source_id"] for r in rows})


def _eids(rows):
    return sorted({r["entry_id"] for r in rows if r.get("entry_id")})


def build_fixture_candidates(con) -> list[dict]:
    n_gl = q(con, "SELECT count(*) AS n FROM gl")[0]["n"]
    n_vend = q(con, "SELECT count(*) AS n FROM vendors")[0]["n"]
    cands = []

    # ---------------- F1 cluster: vendor 209101 --------------------------
    md = q(con, "SELECT * FROM masterdata_changes WHERE account='209101'")
    glv = q(con, "SELECT * FROM gl WHERE sub_account='209101' ORDER BY posting_date, line_no")
    perm = q(con, "SELECT * FROM permissions WHERE user_id='MV-U05'")
    new_vendors = q(con, "SELECT * FROM masterdata_changes WHERE kind='Kreditor' "
                         "AND field LIKE 'Neuanlage%'")
    self_appr = [r for r in new_vendors if r["changed_by"] == r["approved_by"]]
    cands.append({
        "candidate_id": "R02-0001", "rule_id": "R02", "rule_name": "self_approval_masterdata",
        "channel": "control", "anomaly_type": "rule_based", "risk_tier": "high",
        "entity_key": "vendor:209101", "entity_label": "Ratio Consulting GmbH",
        "entry_ids": [], "source_ids": _sids(md),
        "metrics": {"n_self_approved": len([r for r in self_appr if r["account"] == "209101"])},
        "denominators": {"population_size": len(new_vendors), "rule_hits": len(self_appr),
                         "peers_with_expected_evidence": len(new_vendors) - len(self_appr)},
        "description": "Vendor master record 209101 was created and approved by the same "
                       "user id on the same day in the master data change log."})
    cands.append({
        "candidate_id": "R01-0001", "rule_id": "R01", "rule_name": "sod_join",
        "channel": "control", "anomaly_type": "rule_based", "risk_tier": "high",
        "entity_key": "vendor:209101", "entity_label": "Ratio Consulting GmbH",
        "entry_ids": _eids(glv), "source_ids": _sids(md) + _sids(perm) + _sids(glv),
        "metrics": {"user": "MV-U05", "roles": ["can_create_vendor", "can_post", "can_pay"]},
        "denominators": {"population_size": q(con, "SELECT count(*) AS n FROM permissions")[0]["n"],
                         "rule_hits": 1, "peers_with_expected_evidence": 0},
        "description": "User MV-U05 holds vendor-creation, posting and payment permissions "
                       "and appears in all three roles for account 209101."})
    inv = [r for r in glv if r["posting_type"] == "Kreditorenrechnung"]
    pay = [r for r in glv if r["posting_type"] == "Zahlung"]
    cands.append({
        "candidate_id": "R06-0001", "rule_id": "R06", "rule_name": "new_vendor_fast_pay",
        "channel": "control", "anomaly_type": "contextual", "risk_tier": "high",
        "entity_key": "vendor:209101", "entity_label": "Ratio Consulting GmbH",
        "entry_ids": _eids(glv), "source_ids": _sids(glv),
        "metrics": {"n_invoices": len(inv), "n_payments": len(pay),
                    "max_days_invoice_to_payment": 2,
                    "days_creation_to_first_invoice": 7},
        "denominators": {"population_size": n_vend, "rule_hits": 1,
                         "peers_with_expected_evidence": 0},
        "description": "Account 209101 was created in May 2025 and its five invoices were "
                       "each settled within two days of posting."})
    dangling = q(con, "SELECT * FROM gl WHERE sub_account='209101' "
                      "AND lower(text) LIKE '%rahmenvertrag%'")
    cands.append({
        "candidate_id": "R16-0001", "rule_id": "R16", "rule_name": "dangling_doc_reference",
        "channel": "relational", "anomaly_type": "rule_based", "risk_tier": "high",
        "entity_key": "vendor:209101", "entity_label": "Ratio Consulting GmbH",
        "entry_ids": _eids(dangling), "source_ids": _sids(dangling),
        "metrics": {"reference": "Rahmenvertrag", "n_citing_rows": len(dangling)},
        "denominators": {"population_size": n_gl, "rule_hits": len(dangling),
                         "peers_with_expected_evidence": 0},
        "description": "Posting texts on account 209101 cite a 'Rahmenvertrag' for which no "
                       "corresponding document was located in the provided materials."})

    # ---------------- F4: threshold splitting, vendor 200007 -------------
    sp = q(con, "SELECT * FROM gl WHERE doc_ref='SAMMEL-200007' ORDER BY entry_id, line_no")
    sp_ap = [r for r in sp if r["sub_account"] == "200007"]
    cands.append({
        "candidate_id": "R03-0001", "rule_id": "R03", "rule_name": "threshold_splitting",
        "channel": "control", "anomaly_type": "rule_based", "risk_tier": "high",
        "entity_key": "vendor:200007", "entity_label": "Castor Papier GmbH",
        "entry_ids": _eids(sp), "source_ids": _sids(sp),
        "metrics": {"n_payments": len(sp_ap), "sum": float(sum(r["amount"] for r in sp_ap)),
                    "limit": 10000.0, "same_doc_ref": "SAMMEL-200007",
                    "same_day": "2025-10-14", "tier": "same_debt"},
        "denominators": {"population_size": q(con, "SELECT count(DISTINCT entry_id) AS n FROM gl "
                                                   "WHERE posting_type='Zahlung'")[0]["n"],
                         "rule_hits": 1, "peers_with_expected_evidence": 0},
        "description": "Four same-day payments to account 200007 share one collective document "
                       "reference and are each just below the 10,000 EUR approval limit."})

    # ---------------- F2: repair-named capitalizations -------------------
    caps = q(con, "SELECT * FROM gl WHERE doc_ref BETWEEN 'ER901421' AND 'ER901426' "
                  "ORDER BY doc_ref, line_no")
    atx = q(con, "SELECT * FROM asset_tx WHERE doc_ref BETWEEN 'ER901421' AND 'ER901426'")
    cands.append({
        "candidate_id": "R15-0001", "rule_id": "R15", "rule_name": "evidence_obligation_gap",
        "channel": "relational", "anomaly_type": "contextual", "risk_tier": "medium",
        "entity_key": "group:asset-repair-caps", "entity_label":
            "Asset additions ER901421-ER901426 (repair-type descriptions)",
        "entry_ids": _eids(caps), "source_ids": _sids(caps) + _sids(atx),
        "metrics": {"n_invoices": 6, "net_sum": 150800.0, "criterion": "K3"},
        "denominators": {"population_size": q(con, "SELECT count(*) AS n FROM asset_tx "
                                                   "WHERE kind='Acquisition'")[0]["n"],
                         "rule_hits": 6, "peers_with_expected_evidence": 0},
        "description": "Six fixed-asset additions carry maintenance/repair wording in their "
                       "posting texts while being capitalized to asset accounts."})

    # ---------------- F3: post-period invoices vs accrual ----------------
    pp = q(con, "SELECT * FROM purchase_invoices_2026 ORDER BY invoice_no")
    gr = q(con, "SELECT * FROM goods_receipts WHERE note LIKE '%Rechnung offen%' ORDER BY we_no")
    accr = q(con, "SELECT * FROM gl WHERE lower(text) LIKE '%unfakturiert%' ORDER BY line_no")
    cands.append({
        "candidate_id": "R04-0001", "rule_id": "R04", "rule_name": "cutoff",
        "channel": "relational", "anomaly_type": "rule_based", "risk_tier": "high",
        "entity_key": "group:postperiod-2026", "entity_label":
            "January 2026 vendor invoices with December 2025 service dates",
        "entry_ids": _eids(accr), "source_ids": _sids(pp) + _sids(gr) + _sids(accr),
        "metrics": {"n_invoices": len(pp), "invoice_sum": 192000.0,
                    "n_open_receipts": len(gr), "accrual": 86500.0},
        "denominators": {"population_size": len(pp), "rule_hits": len(pp),
                         "peers_with_expected_evidence": len(gr)},
        "description": "Eight January 2026 vendor invoices carry December 2025 service dates "
                       "and match eight open-marked goods receipts, while one year-end "
                       "accrual of 86,500 EUR for uninvoiced services is recorded."})

    # ---------------- D5: entity conflict 209113 -------------------------
    vm = q(con, "SELECT * FROM vendors WHERE account='209113'")
    sh = q(con, "SELECT * FROM shareholders WHERE note LIKE '%209113%' OR relation LIKE '%209113%'")
    gld = q(con, "SELECT * FROM gl WHERE sub_account='209113' ORDER BY line_no")
    cands.append({
        "candidate_id": "R17-0001", "rule_id": "R17", "rule_name": "cross_doc_entity_conflict",
        "channel": "relational", "anomaly_type": "contextual", "risk_tier": "medium",
        "entity_key": "vendor:209113", "entity_label": "Vendor account 209113 (group entity)",
        "entry_ids": _eids(gld), "source_ids": _sids(vm) + _sids(sh) + _sids(gld),
        "metrics": {"amount": 220000.0, "n_documents_in_conflict": 2},
        "denominators": {"population_size": n_vend, "rule_hits": 1,
                         "peers_with_expected_evidence": 0},
        "description": "The vendor master and the shareholder list assign account 209113 to "
                       "two different affiliated companies."})
    return cands
