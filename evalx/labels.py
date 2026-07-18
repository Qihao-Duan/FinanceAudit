"""Three-layer regression labels for the PRACTICE dossier (Muster Verpackungen GmbH).

Encodes PLAN §3.8 exactly. Labels are named after FACTS, not after an
investigation story: rewarding over-accusation in the regression set would
train the pipeline to over-accuse on the finals dossier.

Layer semantics (per target):
  observable_fact       -- directly re-checkable from the dossier data
  accounting_hypothesis -- accounting-treatment judgement (strong evidence,
                           alternative explanations exist)
  intent_hypothesis     -- intent is NOT provable from the data; only
                           indicators may be listed, never asserted

Decoys carry an expected grade:
  likely_benign          -> must never be reported with fraud wording
  insufficient_evidence  -> may be surfaced as observation / PBC request only
  observation_expected   -> (D5 only) the entity conflict is REAL and belongs
                           in the report at observation/conflict level;
                           surfacing it neutrally is CORRECT, accusing it as
                           fraud is a false positive.

Everything here is plain data so evalx.run (and any other module) can import
it without side effects.
"""

# Scheme values come from the closed enum in CONTRACTS §4 / PLAN §3.4.

# Findings whose *scheme itself* implies a fraud accusation when applied to an
# entity (used for decoy false-positive detection; targets are exempt).
FRAUD_IMPLYING_SCHEMES = {"fictitious_vendor"}

# Narrative terms that imply fraud / intent (checked case-insensitively over
# narrative text fields only -- never over the scheme enum field, since
# 'estimate_manipulation' is a legitimate enum value).
FRAUD_WORDING_TERMS = [
    "fraud", "betrug", "betrüger", "betruger",
    "scheinfirma", "scheinrechnung", "fiktiv", "fictitious", "fake vendor",
    "veruntreu", "unterschlag", "embezzle", "kickback", "bestech",
    "vorsätzlich", "vorsatz", "deliberate", "intentional",
    "verschleier", "conceal", "täuschung", "tauschung",
    "manipulier", "manipulat",
]

LOCK_DATE = "2026-01-20"   # Festschreibung
FY = 2025

# --------------------------------------------------------------------------
# Targets F1-F4
# --------------------------------------------------------------------------
# entity_tokens_numeric are matched with digit boundaries (so '209101' does
# not match inside '1209101'); entity_tokens_text are lowercase substrings.
# A finding HITS a target iff disposition == 'report', scheme in
# scheme_family, and >= 1 entity token matches. Candidate coverage needs only
# a token match (rule ids, not schemes, live at candidate level).

TARGETS = [
    {
        "target_id": "F1",
        "name": "Vendor-control anomaly 209101 Ratio Consulting",
        "observable_fact": (
            "Vendor account 209101 (Ratio Consulting) was created and approved "
            "by the same user MV-U05 on 12.05.2025 (SoD conflict per the "
            "permissions matrix); 5 consulting invoices net 248,000.00 EUR "
            "(gross 295,120.00 EUR) were each paid within 2 days; posting texts "
            "cite a 'Rahmenvertrag' that resolves to no document in the dossier."
        ),
        "accounting_hypothesis": (
            "Substantiation for the five consulting invoices is incomplete: the "
            "cited framework contract is absent from the provided material and "
            "no service-acceptance evidence exists within the declared scope."
        ),
        "intent_hypothesis": (
            "Whether 209101 is a fictitious vendor cannot be proven from the "
            "data; indicators (control bypass, dangling contract reference, "
            "fast payment) may be listed, not asserted."
        ),
        "scheme_family": ["fictitious_vendor", "controls_breach"],
        "entity_tokens_numeric": ["209101"],
        "entity_tokens_text": ["ratio consulting"],
        "amount_assertion": {
            "kind": "exact",
            "value": 248000.00,
            "unit": "EUR",
            "what": "net sum of the 5 consulting invoices",
            "gating": True,
        },
    },
    {
        "target_id": "F2",
        "name": "Repair-named asset capitalizations 150,800",
        "observable_fact": (
            "Six asset capitalizations with repair-language descriptions, net "
            "150,800.00 EUR, invoices ER901421-ER901426, gross = net * 1.19."
        ),
        "accounting_hypothesis": (
            "Repair expense vs. capitalizable component replacement is "
            "unresolved; classification as capitalization lacks a technical "
            "assessment in the provided material."
        ),
        "intent_hypothesis": (
            "Whether the capitalization was chosen to inflate profit is not "
            "provable from the data."
        ),
        "scheme_family": ["expense_capitalization"],
        "entity_tokens_numeric": [],
        "entity_tokens_text": [
            "er901421", "er901422", "er901423",
            "er901424", "er901425", "er901426",
        ],
        "amount_assertion": {
            "kind": "exact",
            "value": 150800.00,
            "unit": "EUR",
            "what": "net sum of the 6 capitalized invoices",
            "gating": False,   # contract mandates exact gating only for F1/F4
        },
    },
    {
        "target_id": "F3",
        "name": "Post-period invoices vs. accrual, potential unmatched liability",
        "observable_fact": (
            "Eight January-2026 vendor invoices (vendors 209130-209137) with "
            "service dates in December 2025 totalling 192,000.00 EUR match 8 "
            "goods receipts marked 'Dez-Lieferung, Rechnung offen'; only an "
            "86,500.00 EUR accrual 'unfakturierte Leistungen' exists, and no "
            "subset of the 8 invoices sums to it."
        ),
        "accounting_hypothesis": (
            "Potential unmatched liability in [105,500.00, 192,000.00] EUR. The "
            "relation of the 86,500 accrual to the 8 invoices is undetermined "
            "(estimate / partial coverage / different net-tax basis possible). "
            "NOT to be stated as profit impact -- liability, purchases and P&L "
            "effect are three different concepts."
        ),
        "intent_hypothesis": (
            "Whether the accrual shortfall is deliberate is not provable from "
            "the data."
        ),
        "scheme_family": ["cutoff"],
        "entity_tokens_numeric": [
            "209130", "209131", "209132", "209133",
            "209134", "209135", "209136", "209137",
        ],
        "entity_tokens_text": ["dez-lieferung, rechnung offen"],
        "amount_assertion": {
            "kind": "range",
            "min": 105500.00,
            "max": 192000.00,
            "unit": "EUR",
            "what": "potential unmatched liability",
            "required_phrases": [
                "potential unmatched liability",
                "potenzielle unerfasste verbindlichkeit",
                "potenzielle nicht erfasste verbindlichkeit",
                "unmatched liability",
            ],
            "gating": True,
        },
    },
    {
        "target_id": "F4",
        "name": "Below-threshold payment split, vendor 200007",
        "observable_fact": (
            "Four payments of 9,780 / 9,820 / 9,750 / 9,690 EUR on 14.10.2025 "
            "to vendor 200007, document SAMMEL-200007, posting text "
            "'Teilzahlung Lieferantenrechnung', user MV-U11; each below the "
            "10,000 EUR dual-approval limit, total 39,040.00 EUR."
        ),
        "accounting_hypothesis": (
            "One original debt (same SAMMEL document) split into partial "
            "payments each below the approval threshold."
        ),
        "intent_hypothesis": (
            "Whether the split was a deliberate circumvention of the "
            "dual-approval control is not provable from the data."
        ),
        "scheme_family": ["threshold_splitting", "controls_breach"],
        "entity_tokens_numeric": ["200007"],
        "entity_tokens_text": ["sammel-200007"],
        "amount_assertion": {
            "kind": "exact",
            "value": 39040.00,
            "unit": "EUR",
            "what": "sum of the 4 same-day payments",
            "gating": True,
        },
    },
]

# --------------------------------------------------------------------------
# Decoys D1-D7 (false-positive traps)
# --------------------------------------------------------------------------
# A decoy counts as a FALSE POSITIVE only if some finding matching its tokens
# has disposition == 'report' AND fraud-implying wording/scheme.
# Observation-level or neutral mention is allowed (for D5 it is expected).

DECOYS = [
    {
        "decoy_id": "D1",
        "name": "480k machine investment, Investitionsantrag IA-2025-04 absent",
        "grade": "insufficient_evidence",
        "tokens_numeric": ["480000"],
        "tokens_text": ["ia-2025-04"],
        "note": (
            "GL references investment application IA-2025-04; the document is "
            "not in the dossier. Correct handling: dangling-reference "
            "observation / PBC request, never a fraud accusation."
        ),
    },
    {
        "decoy_id": "D2",
        "name": "Twin logistics vendors Nord Logistik 209110 / Nordlicht Logistik 209111",
        "grade": "likely_benign",
        "tokens_numeric": ["209110", "209111"],
        "tokens_text": ["nord logistik", "nordlicht"],
        "note": (
            "Similar names but distinct USt-IDs and both have goods receipts "
            "(ER901436-ER901441). Duplicate-vendor accusation would be a FP."
        ),
    },
    {
        "decoy_id": "D3",
        "name": "Compliant new vendor Vega Werkstoffe 209112",
        "grade": "likely_benign",
        "tokens_numeric": ["209112"],
        "tokens_text": ["vega werkstoffe"],
        "note": "Four-eyes-approved creation plus 4 goods receipts.",
    },
    {
        "decoy_id": "D4",
        "name": "22 year-end customer rebates 691,260 recorded 08.01.2026",
        "grade": "likely_benign",
        "tokens_numeric": ["691260"],
        "tokens_text": ["rückvergütung", "rueckverguetung", "jahresbonus", "rebate"],
        "note": (
            "Entries dated 31.12.2025 recorded 08.01.2026, before the "
            "Festschreibung lock date 2026-01-20 = normal closing entries, not "
            "backdating. Rebate basis may be listed as pending verification."
        ),
    },
    {
        "decoy_id": "D5",
        "name": "Konzernumlage 220,000 via vendor account 209113 -- entity conflict",
        "grade": "observation_expected",
        "tokens_numeric": ["209113"],
        "tokens_text": ["konzernumlage", "muster verpackung austria"],
        "note": (
            "REAL conflict: Gesellschafterliste line 6 assigns 'Personenkonto "
            "Kreditor 209113' to sister company Muster Verpackung Austria GmbH "
            "while the vendor master names parent Muster Beteiligungs GmbH. "
            "Surfacing this as an observation/conflict is CORRECT behaviour and "
            "is credited; accusing it as fraud is a false positive."
        ),
    },
    {
        "decoy_id": "D6",
        "name": "Asset disposal, loss 110,395 booked to 677000",
        "grade": "insufficient_evidence",
        "tokens_numeric": ["677000", "110395"],
        "tokens_text": ["anlagenabgang"],
        "note": (
            "No independent disposal certificate in the dossier -> "
            "insufficient evidence, observation/PBC only."
        ),
    },
    {
        "decoy_id": "D7",
        "name": "Invoice AR502040 + credit note SG502041 offsetting pair (net 18,500)",
        "grade": "likely_benign",
        "tokens_numeric": [],
        "tokens_text": ["ar502040", "sg502041"],
        "note": "Matched invoice/credit-note pair; offsetting is legitimate.",
    },
]

TARGET_INDEX = {t["target_id"]: t for t in TARGETS}
DECOY_INDEX = {d["decoy_id"]: d for d in DECOYS}
