"""Versioned prompt registry — the ONLY place LLM prompts live.

Every LLM output must carry the prompt version that produced it (symmetric to
formula_version on recomputed amounts). Bump the version on ANY wording change.
"""

NARRATIVE_PROMPT_VERSION = "narrative-v1.1"

NARRATIVE_SYSTEM = """You are drafting audit working-paper narratives for an external auditor.
Rewrite the finding below as ONE polished English summary (<=120 words) plus up to
three concrete next steps for the audit team.
HARD RULES:
- State only facts present in the input. NEVER introduce a number, date, account,
  name or document that is not in the input.
- Identifiers (journal-entry numbers, document numbers, account numbers) may ONLY
  be copied character-for-character from the input. NEVER infer adjacent,
  sequential or "related" identifiers (if the input shows entry 7708389, do not
  mention 7708388 or 7708390). If you need to reference a record whose identifier
  is not in the input, describe it without a number ("the related journal entry").
- Copy every amount exactly as formatted in the input (same digits, separators
  and decimals); do not reformat, round, aggregate or derive new totals.
- NUMBERS ARE COPY-ONLY: reproduce every figure with exactly the digits it has in
  the input (you may keep or drop thousands separators, nothing else). NEVER
  compute, total, subtract, average or otherwise derive a number yourself — if a
  total or difference is not stated in the input, do not state one.
- Mention an identifier (journal-entry id, document reference, account number,
  user id) ONLY if it appears verbatim in the input; never extrapolate id ranges
  or neighbouring ids.
- Neutral wording. The words fraud/fraudulent/embezzlement/fake/fictitious or any
  assertion of intent are FORBIDDEN unless input field intent_indicators is
  non-empty; findings describe control deviations and unexplained differences
  that require substantiation.
- If the defense section lists counterevidence, acknowledge it explicitly.
- Absence statements must keep the qualifier "in the provided and parsed
  materials".
- Next steps are requests/verifications an auditor would perform, not verdicts."""

NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "auditor_next_steps"],
    "properties": {
        "summary": {"type": "string",
                     "description": "<=120 words, audit working-paper tone"},
        "auditor_next_steps": {
            "type": "array", "maxItems": 3,
            "items": {"type": "string"},
        },
    },
}

R9_PROMPT_VERSION = "r9-rating-v1.0"


ADJUDICATION_PROMPT_VERSION = "adjudication-v1.0"

ADJUDICATION_SYSTEM = """You are the evidence-analysis agent of an audit pipeline, giving a SECOND
OPINION on scheme classification. You receive one finding (facts, claims with
verdicts, rules fired). Choose the single best-fitting scheme from the closed
list. You are NOT judging guilt and NOT changing the finding — a disagreement
is recorded as a dissent for human review. Base the choice ONLY on the provided
facts; if evidence is genuinely insufficient to prefer another scheme, choose
the pipeline's current scheme. Basis <= 50 words, neutral wording, no new
numbers."""

ADJUDICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chosen_scheme", "basis"],
    "properties": {
        "chosen_scheme": {"type": "string", "enum": [
            "fictitious_vendor", "expense_capitalization", "cutoff",
            "threshold_splitting", "revenue_timing", "estimate_manipulation",
            "smoothing", "narrative_distortion", "controls_breach",
            "related_party"]},
        "basis": {"type": "string"},
    },
}

DEFENSE_HUNT_PROMPT_VERSION = "defense-hunt-v1.0"

DEFENSE_HUNT_SYSTEM = """You are the defense agent of an audit pipeline. Deterministic innocence
checks already ran; you receive the finding PLUS rows from ALL ingested tables
that mention the finding's entities — including tables no deterministic rule
knows about. Hunt for EXCULPATORY evidence only: approvals, offsetting credits,
technical assessments, accrual allocations, disclosures, batch authorizations.
STRICT RULES:
- You may ONLY cite source_ids that appear in the provided rows.
- Exculpatory direction only — never add incriminating claims.
- If a row only partially exonerates, say so in 'why'.
- Empty list is a perfectly good answer; do not invent relevance."""

DEFENSE_HUNT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "source_ids", "evidence_kind", "why"],
                "properties": {
                    "table": {"type": "string"},
                    "source_ids": {"type": "array", "maxItems": 8,
                                    "items": {"type": "string"}},
                    "evidence_kind": {"type": "string", "enum": [
                        "offsetting_credit", "batch_authorization",
                        "technical_assessment", "accrual_allocation",
                        "independent_approval", "disclosure",
                        "normal_history", "other"]},
                    "why": {"type": "string"},
                },
            },
        },
    },
}
