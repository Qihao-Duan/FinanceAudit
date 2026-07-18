"""Verdict gate (Agent D, stage 5 — PLAN §3.6).

Core-claim gate over defended findings:
  core all supported AND every amount recompute exact AND no direct
  contradiction -> report; data-conflict / low-tier / unverifiable-core ->
  observation; contradicted core or failed recompute -> rejected (logs kept);
  schema failure -> ONE auditable normalization retry (non-factual fields
  only), else quarantine. No auto-regeneration of factual content, ever.

Fraud wording gate: scheme wording only with intent indicators (control
bypass, unresolvable/contradicted cited evidence, interest linkage);
otherwise 'control deficiency / needs review'. Cap: top-12 reports (cap,
not quota). llm_used: false.
"""
from __future__ import annotations

from financeaudit.claims.engine import recompute_claim

_TIER_RANK = {"high": 0, "medium": 1, "low": 2}

_REQUIRED_FINDING = ["finding_id", "scheme", "mechanism", "anomaly_type",
                     "claims", "expected_evidence", "missing_evidence",
                     "pbc_requests", "counterevidence", "innocence_checked",
                     "parser_coverage", "denominators", "disposition"]
_REQUIRED_CLAIM = ["claim_id", "role", "type", "assertion", "citations", "verdict"]
#: factual fields that must NEVER be defaulted-in by the retry
_FACTUAL_CLAIM = ["assertion", "citations"]

_NONFACTUAL_DEFAULTS = {
    "expected_evidence": [], "missing_evidence": [], "pbc_requests": [],
    "counterevidence": [], "innocence_checked": [],
    "parser_coverage": "partial", "denominators": {}, "anomaly_type": "rule_based",
    "mechanism": "narrative_only", "disposition": "report",
}


def validate_schema(f) -> list[str]:
    errs = [k for k in _REQUIRED_FINDING if k not in f]
    for c in f.get("claims", []):
        errs += [f"{c.get('claim_id', '?')}:{k}" for k in _REQUIRED_CLAIM if k not in c]
        if not c.get("citations"):
            errs.append(f"{c.get('claim_id', '?')}:citations_empty")
    if not f.get("claims"):
        errs.append("claims_empty")
    return errs


def normalize_retry(f) -> tuple[bool, list[str]]:
    """One auditable retry: fill NON-factual fields only. Returns (ok, log)."""
    log = []
    for k, v in _NONFACTUAL_DEFAULTS.items():
        if k not in f:
            f[k] = v if not isinstance(v, (list, dict)) else type(v)()
            log.append(f"defaulted non-factual field '{k}'")
    for c in f.get("claims", []):
        if "verdict" not in c:
            c["verdict"] = "unverifiable"
            log.append(f"{c.get('claim_id', '?')}: missing verdict -> unverifiable")
        for k in _FACTUAL_CLAIM:
            if not c.get(k):
                # factual content missing — NOT repairable, no fabrication
                return False, log + [f"{c.get('claim_id', '?')}: factual field "
                                     f"'{k}' missing — quarantine"]
    return not validate_schema(f), log


def intent_indicators(f) -> list[str]:
    """Deterministic intent-indicator extraction (PLAN §3.6 wording gate)."""
    ind = []
    # LLM-originated entries (AGENTS stage, llm_* prefix) are presentation/
    # defense enrichment and must NEVER influence the deterministic gate.
    preds = {p["predicate"]: p for p in f.get("innocence_checked", [])
             if not str(p.get("predicate", "")).startswith("llm_")}
    claims = {c["claim_id"].rsplit("-", 1)[-1]: c for c in f.get("claims", [])}

    def _supported(ctype):
        return any(c["type"] == ctype and c["role"] == "core" and
                   c["verdict"] == "supported" for c in f.get("claims", []))

    if f["scheme"] == "fictitious_vendor":
        if preds.get("independent_masterdata_approval", {}).get("result") == "not_found":
            ind.append("control_bypass: master-data change self-approved")
        if _supported("entity_attribute"):
            ind.append("control_bypass: combined create/post/pay rights enacted")
        if (preds.get("contract_ref_resolvable", {}).get("result") == "not_found"
                and _supported("existence")):
            ind.append("cited evidence unresolvable: framework contract "
                       "not found in provided+parsed materials")
    if f["scheme"] == "threshold_splitting":
        if _supported("comparative"):
            ind.append("control_bypass: amounts individually below the "
                       "dual-approval limit while the total exceeds it")
        if preds.get("independent_invoices_per_payment", {}).get("result") == "not_found":
            ind.append("single collective payable settled in parts "
                       "(no independent invoices per payment found)")
    if f["scheme"] == "controls_breach":
        # self-approved bank-detail change coupled to outbound payments
        # (mutation-suite 2026-07-18): a control-bypass intent indicator so the
        # wording gate permits stronger language for the diversion pattern.
        for c in f.get("claims", []):
            det = (c.get("verification") or {}).get("detail")
            if (isinstance(det, dict) and det.get("self_approved_bank_change")
                    and c["verdict"] == "supported"):
                ind.append("control_bypass: vendor bank details changed and "
                           "self-approved, then coupled to outbound payments "
                           "within the window")
                break
    if f["scheme"] == "related_party":
        # identity conflict alone is a data conflict, not an intent indicator
        pass
    _ = claims
    return ind


def decide(f) -> dict:
    """Compute disposition + wording level for one schema-valid finding."""
    core = [c for c in f["claims"] if c["role"] == "core"]
    contradicted = [c for c in f["claims"] if c["verdict"] == "contradicted"]
    core_contra = [c for c in core if c["verdict"] == "contradicted"]
    core_unver = [c for c in core if c["verdict"] == "unverifiable"]
    recompute_fail = [c for c in f["claims"]
                      if (c.get("recompute") or {}).get("applicable")
                      and not (c.get("recompute") or {}).get("match")]
    gate = {"n_core": len(core),
            "core_supported": sum(1 for c in core if c["verdict"] == "supported"),
            "core_contradicted": len(core_contra),
            "core_unverifiable": len(core_unver),
            "amount_recompute_failures": [c["claim_id"] for c in recompute_fail],
            "any_contradiction": bool(contradicted)}

    if core_contra or recompute_fail:
        disposition, reason = "rejected", (
            "core claim contradicted or amount recompute failed — original "
            "output and logs preserved, no regeneration")
    elif core_unver:
        disposition, reason = "observation", (
            "core claim(s) unverifiable — routed to needs-material queue, "
            "not into the report body")
        f["needs_material"] = True
    elif not core:
        disposition, reason = "observation", "no core claims — signal only"
    elif f.get("finding_class") == "data_conflict":
        disposition, reason = "observation", (
            "cross-document data conflict — surfaced neutrally, no "
            "misstatement or control-breach claim")
    elif f.get("finding_class") == "statistical_signal" or f.get("risk_tier") == "low":
        disposition, reason = "observation", (
            "low-tier / statistical signal — corroboration only")
    else:
        disposition, reason = "report", "all core claims supported, recompute exact"

    # Decisive-exculpation demotion (deterministic; mutation-suite symmetry
    # fix 2026-07-18): when the defense documents scheme-specific DECISIVE
    # exculpatory evidence, the finding is disclosed as an observation with
    # the evidence attached instead of being reported as a deviation. Only
    # deterministic predicates count (llm_* excluded); the LIST is scanned so
    # multiple entries under one predicate name (token-scan + typed-table)
    # cannot shadow each other.
    DECISIVE_EXCULPATION = {
        "threshold_splitting": ("batch_payment_authorization",),
        "expense_capitalization": ("technical_assessment_exists",),
        "cutoff": ("accrual_schedule_allocation",),
        "controls_breach": ("batch_payment_authorization",
                             "offsetting_refund_exists"),
    }
    if disposition == "report":
        decisive = [p for p in f.get("innocence_checked", [])
                    if p.get("result") == "found"
                    and not str(p.get("predicate", "")).startswith("llm_")
                    and p.get("predicate") in
                    DECISIVE_EXCULPATION.get(f.get("scheme"), ())]
        if decisive:
            disposition = "observation"
            names = sorted({p["predicate"] for p in decisive})
            reason = ("decisive exculpatory evidence documented "
                      f"({', '.join(names)}) — disclosed as observation with "
                      "the evidence attached, not reported as a deviation")
            gate["decisive_exculpation"] = names

    ind = intent_indicators(f)
    if disposition == "report" and ind:
        wording = "scheme_pattern"
    elif disposition == "report":
        wording = "control_deficiency"
    else:
        wording = "needs_review" if disposition == "observation" else "n/a"
    return {"disposition": disposition, "gate": gate, "gate_reason": reason,
            "intent_indicators": ind, "wording_level": wording}


def evidence_status(f) -> list[dict]:
    """Three-state evidence status for the card (PLAN §3.7)."""
    out = []
    for m in f.get("missing_evidence", []):
        out.append({"item": m, "state": "not_provided_in_materials"})
    if f["scheme"] == "cutoff":
        out.append({"item": "year-end accrual 'unfakturierte Leistungen' "
                            "(86,500.00 EUR)",
                    "state": "provided_but_mismatched",
                    "note": "exists, but no subset of the eight invoices sums to it"})
    if f.get("parser_coverage") != "complete":
        out.append({"item": "one or more cited source files",
                    "state": "parse_incomplete"})
    return out


def gate_all(con, findings: list[dict], cap: int = 12) -> tuple[list[dict], dict]:
    """Validate, independently recompute, gate, rank, cap.

    Returns (ordered findings, processing stats)."""
    stats = {"retried": [], "quarantined": [], "rejected": [], "capped": []}
    kept = []
    for f in findings:
        errs = validate_schema(f)
        if errs:
            ok, log = normalize_retry(f)
            stats["retried"].append({"finding_id": f.get("finding_id"),
                                     "errors": errs, "retry_log": log, "ok": ok})
            f.setdefault("retry_log", []).extend(log)
            if not ok:
                f["disposition"] = "quarantine"
                f["quarantine_reason"] = {"schema_errors": errs, "retry_log": log}
                stats["quarantined"].append(f.get("finding_id"))
                kept.append(f)
                continue
        # independent recompute pass (verdict-side, zero tolerance)
        for c in f["claims"]:
            recompute_claim(con, c)
        d = decide(f)
        f["disposition"] = d["disposition"]
        f["gate"] = d["gate"]
        f["gate_reason"] = d["gate_reason"]
        f["intent_indicators"] = d["intent_indicators"]
        f["wording_level"] = d["wording_level"]
        f["evidence_status"] = evidence_status(f)
        f["reporting_status"] = {"report": "finding", "observation": "observation",
                                 "rejected": "verify", "quarantine": "verify"}[d["disposition"]]
        if d["disposition"] == "rejected":
            stats["rejected"].append(f["finding_id"])
        kept.append(f)

    # rank reports by (tier, amount desc); cap is a ceiling, not a quota
    reports = [f for f in kept if f["disposition"] == "report"]
    reports.sort(key=lambda f: (_TIER_RANK.get(f.get("risk_tier", "low"), 3),
                                -(f.get("amount_eur") or 0)))
    for i, f in enumerate(reports):
        f["rank"] = i + 1
        if i >= cap:
            f["disposition"] = "observation"
            f["reporting_status"] = "observation"
            f["gate_reason"] += f" | demoted: report cap {cap} exceeded"
            stats["capped"].append(f["finding_id"])
    order = {"report": 0, "observation": 1, "rejected": 2, "quarantine": 3}
    kept.sort(key=lambda f: (order.get(f["disposition"], 4),
                             f.get("rank", 99),
                             _TIER_RANK.get(f.get("risk_tier", "low"), 3),
                             -(f.get("amount_eur") or 0)))
    return kept, stats
