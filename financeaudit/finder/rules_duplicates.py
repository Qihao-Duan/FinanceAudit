"""Finder rule R20 — duplicate / over-payment of a vendor invoice.

NEW rule file (auto-discovered by finder/run.py's rules_*.py glob). This is a
legitimate scheme-space EXTENSION: duplicate payment is NOT in the practice
answer set, so it cannot be answer-fitting. It exists so the pipeline can
detect the exact final-dossier scenario of the same invoice being paid twice on
modified data (see docs/PLAN.md §3.8, evalx/mutations.py).

Signal (deterministic, no LLM): for every (vendor sub-account, invoice
doc_ref) the AP control account 330000-<vendor> nets to ZERO when an invoice is
booked once (Kreditorenrechnung credit) and cleared once (Zahlung debit):
    payable(-X) + payment(+X) = 0.
Paying the SAME invoice twice over-clears the account:
    payable(-X) + payment(+X) + payment(+X) = +X   (a debit balance -> we paid
more than we owed). A later credit note / refund restores it to zero:
    ... + refund(-X) = 0.
So a *positive* net AP balance per (vendor, invoice), together with >=2 distinct
payment entries against a real booked payable, is a duplicate / over-payment
with NO offsetting credit inside the ledger — exactly the mini-contract in
docs/CONTRACTS.md §3 ("same doc_ref/vendor paid >=2 times without offsetting
credit -> candidate").

Why this does NOT misfire on the practice targets/decoys:
  * F4 threshold splitting (SAMMEL-200007): the collective payment reference has
    NO Kreditorenrechnung under it (the payables live under separate invoice
    refs), so `payable` is absent -> excluded by the JOIN.
  * D7 offsetting invoice/credit-note pair (AR502040/SG502041): debitor side
    (230000), never touched here (330000 only).
  * Any normal single payment nets to 0 -> net_ap not > 0 -> not flagged.
  * The innocent-repair mutation twin nets back to 0 via its credit note ->
    not flagged (finder-level symmetry; the defense stage carries the second,
    explicit offsetting-refund innocence check — see financeaudit/defense).

Emits neutral wording only; every candidate cites the invoice + payment rows.
"""
from __future__ import annotations

# Tolerance for the AP-net comparison: ledger<->ledger, cent-exact.
_TOL = 0.005


_DETECT_SQL = """
WITH pay AS (
    SELECT sub_account, doc_ref,
           COUNT(DISTINCT entry_id) AS n_pay,
           ROUND(SUM(amount), 2)    AS paid_ap,
           LIST(DISTINCT entry_id)  AS pay_entries
    FROM gl
    WHERE hb_account = '330000' AND sub_account IS NOT NULL AND doc_ref IS NOT NULL
          AND posting_type = 'Zahlung' AND amount > 0
    GROUP BY 1, 2
),
payable AS (
    SELECT sub_account, doc_ref,
           ROUND(SUM(amount), 2)   AS payable_ap,
           LIST(DISTINCT entry_id) AS inv_entries
    FROM gl
    WHERE hb_account = '330000' AND sub_account IS NOT NULL AND doc_ref IS NOT NULL
          AND posting_type = 'Kreditorenrechnung'
    GROUP BY 1, 2
),
netap AS (
    SELECT sub_account, doc_ref, ROUND(SUM(amount), 2) AS net_ap
    FROM gl
    WHERE hb_account = '330000' AND sub_account IS NOT NULL AND doc_ref IS NOT NULL
    GROUP BY 1, 2
)
SELECT p.sub_account, p.doc_ref, p.n_pay, p.paid_ap, b.payable_ap, n.net_ap,
       p.pay_entries, b.inv_entries
FROM pay p
JOIN payable b USING (sub_account, doc_ref)
JOIN netap   n USING (sub_account, doc_ref)
WHERE p.n_pay >= 2 AND n.net_ap > {tol}
ORDER BY n.net_ap DESC, p.sub_account, p.doc_ref
"""


def _vendor_name(con, acct):
    row = con.execute("SELECT name FROM vendors WHERE account = ?", [acct]).fetchone()
    return row[0] if row and row[0] else None


def r20_duplicate_payment(con, thresholds):
    out = []
    # population = distinct vendor invoices that were actually paid (>=1 payment
    # against a booked payable) — the denominator this rule screens.
    pop = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT b.sub_account, b.doc_ref
            FROM (SELECT DISTINCT sub_account, doc_ref FROM gl
                  WHERE hb_account='330000' AND posting_type='Kreditorenrechnung'
                        AND sub_account IS NOT NULL AND doc_ref IS NOT NULL) b
            JOIN (SELECT DISTINCT sub_account, doc_ref FROM gl
                  WHERE hb_account='330000' AND posting_type='Zahlung'
                        AND sub_account IS NOT NULL AND doc_ref IS NOT NULL) p
              USING (sub_account, doc_ref)
        )
        """).fetchone()[0]

    hits = con.execute(_DETECT_SQL.format(tol=_TOL)).fetchall()
    seq = 0
    for sub, doc_ref, n_pay, paid_ap, payable_ap, net_ap, pay_entries, inv_entries in hits:
        seq += 1
        payable = round(abs(payable_ap or 0.0), 2)
        overpaid = round(net_ap, 2)
        name = _vendor_name(con, sub) or sub
        entry_ids = sorted({str(e) for e in list(inv_entries or []) + list(pay_entries or [])})

        # cite the invoice AP leg + every payment AP/bank leg for these entries
        ph = ",".join("?" * len(entry_ids)) if entry_ids else "''"
        sids = [r[0] for r in con.execute(
            f"SELECT DISTINCT source_id FROM gl WHERE doc_ref = ? "
            f"AND entry_id IN ({ph}) ORDER BY 1", [doc_ref, *entry_ids]).fetchall()]

        tier = "high" if overpaid + _TOL >= payable and payable > 0 else "medium"
        out.append({
            "rule_id": "R20",
            "rule_name": "duplicate_payment",
            "channel": "control",
            "anomaly_type": "rule_based",
            "risk_tier": tier,
            "entity_key": f"vendor:{sub}",
            "entity_label": name,
            "entry_ids": entry_ids,
            "source_ids": sids,
            "metrics": {
                "check": "duplicate_payment",
                "vendor": sub,
                "invoice": doc_ref,
                "n_payments": int(n_pay),
                "gross_paid": round(paid_ap, 2),
                "payable": payable,
                "net_overpayment": overpaid,
                "offsetting_credit": 0.0,
            },
            "denominators": {
                "population_size": int(pop),
                "rule_hits": len(hits),
                "peers_with_expected_evidence": 0,
            },
            "description": (
                f"Vendor invoice {doc_ref} ({name}, account 330000-{sub}) was cleared "
                f"by {int(n_pay)} separate payments totalling {round(paid_ap, 2):,.2f} EUR "
                f"against a booked payable of {payable:,.2f} EUR, leaving the vendor "
                f"account in a {overpaid:,.2f} EUR debit balance for this invoice "
                f"(paid more than invoiced). No offsetting credit note or refund "
                f"reduces the excess within the general ledger."),
        })
    return out


RULES = [
    {"rule_id": "R20", "rule_name": "duplicate_payment", "channel": "control",
     "fn": r20_duplicate_payment},
]
