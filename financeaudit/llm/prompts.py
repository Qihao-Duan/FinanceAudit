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
