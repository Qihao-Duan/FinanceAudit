"""LLM enrichment stage (post-verdict, pre-eval): polished narratives +
auditor next steps for gated findings.

    python3 -m financeaudit.llm.enrich [--build build]

Strictly presentation-layer (PLAN §2.3): dispositions, claims, verdicts,
amounts and citations are FROZEN inputs. The LLM output is post-filtered:
- any digit token not present in the finding's own facts  -> rejected
- fraud wording without intent indicators                 -> rejected
On rejection or API failure the deterministic description stays and the
finding keeps llm_used=false. Without OPENAI_API_KEY the stage is a no-op.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from financeaudit.core import config
from financeaudit.llm.client import structured_call, extract_digit_tokens
from financeaudit.llm.calllog import log_event
from financeaudit.llm.prompts import (
    NARRATIVE_SYSTEM as SYSTEM,
    NARRATIVE_SCHEMA as SCHEMA,
    NARRATIVE_PROMPT_VERSION,
)

_LEGACY_SCHEMA = {
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

_LEGACY_SYSTEM = None  # moved to financeaudit/llm/prompts.py

FRAUD_TERMS_FALLBACK = ["fraud", "fraudulent", "embezzle", "fake", "fictitious",
                        "kickback", "theft", "stole", "criminal"]


def _fraud_terms():
    try:
        from evalx.labels import FRAUD_WORDING_TERMS
        return list(FRAUD_WORDING_TERMS)
    except Exception:
        return FRAUD_TERMS_FALLBACK


def _finding_payload(f: dict) -> dict:
    return {
        "title": f.get("title"),
        "scheme": f.get("scheme"),
        "disposition": f.get("disposition"),
        "entity": f.get("entity_label"),
        "deterministic_description": f.get("description"),
        "claims": [{"role": c.get("role"), "type": c.get("type"),
                     "verdict": c.get("verdict"),
                     "assertion": c.get("assertion"),
                     "value": (c.get("value") or {}).get("value")}
                    for c in f.get("claims", [])],
        "defense": {"innocence_checked": f.get("innocence_checked", []),
                     "counterevidence": f.get("counterevidence", [])},
        "intent_indicators": f.get("intent_indicators", []),
        "missing_evidence": f.get("missing_evidence", []),
        "denominators": f.get("denominators", {}),
    }


def enrich(build_dir: Path) -> int:
    fpath = build_dir / "findings.json"
    data = json.loads(fpath.read_text(encoding="utf-8"))
    findings = data["findings"] if isinstance(data, dict) else data
    if not config.llm_available():
        print("[enrich] OPENAI_API_KEY not set -> stage skipped (llm_used stays false)")
        return 0

    fraud_terms = _fraud_terms()
    stats = {"calls": 0, "accepted": 0, "rejected_numbers": 0,
             "rejected_wording": 0, "failed": 0}
    for f in findings:
        payload = _finding_payload(f)
        model = (config.OPENAI_MODEL_REASONING if f.get("disposition") == "report"
                 else config.OPENAI_MODEL_FAST)
        out = structured_call(
            model, SYSTEM,
            json.dumps(payload, ensure_ascii=False, default=str),
            "finding_narrative", SCHEMA,
            context={"finding_id": f.get("finding_id"),
                     "disposition": f.get("disposition"),
                     "prompt_version": NARRATIVE_PROMPT_VERSION})
        stats["calls"] += 1
        if out is None:
            stats["failed"] += 1
            log_event("filter_decision", {"finding_id": f.get("finding_id"),
                                           "decision": "failed_api"})
            continue
        usage = out.pop("_usage", {})
        # post-filter 1: no invented numbers
        allowed = extract_digit_tokens(payload)
        produced = extract_digit_tokens({"s": out.get("summary"),
                                          "n": out.get("auditor_next_steps")})
        if not produced.issubset(allowed):
            stats["rejected_numbers"] += 1
            log_event("filter_decision", {"finding_id": f.get("finding_id"),
                                           "decision": "rejected_numbers",
                                           "offending_tokens": sorted(produced - allowed)[:10]})
            continue
        # post-filter 2: no fraud wording without intent indicators
        text = (out.get("summary", "") + " " +
                " ".join(out.get("auditor_next_steps", []))).lower()
        if not payload["intent_indicators"] and any(t in text for t in fraud_terms):
            stats["rejected_wording"] += 1
            log_event("filter_decision", {"finding_id": f.get("finding_id"),
                                           "decision": "rejected_wording"})
            continue
        f["description_llm"] = out["summary"]
        f["next_steps_llm"] = out.get("auditor_next_steps", [])
        f["llm"] = {"model": usage.get("model", model), "usage": usage,
                     "prompt_version": NARRATIVE_PROMPT_VERSION,
                     "constraints": "facts_locked; post-filtered"}
        f["llm_used"] = True
        stats["accepted"] += 1
        log_event("filter_decision", {"finding_id": f.get("finding_id"),
                                       "decision": "accepted",
                                       "prompt_version": NARRATIVE_PROMPT_VERSION})

    if isinstance(data, dict):
        meta = data.setdefault("meta", {})
        meta["llm_enrichment"] = {
            "models": {"report": config.OPENAI_MODEL_REASONING,
                        "observation": config.OPENAI_MODEL_FAST},
            "prompt_version": NARRATIVE_PROMPT_VERSION,
            **stats,
        }
        meta["llm_used"] = stats["accepted"] > 0
    tmp = fpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    tmp.replace(fpath)
    # re-render the static report so it carries the enriched narratives
    try:
        from financeaudit.verdict.report_html import render as _render
        meta_for_report = data.get("meta", {}) if isinstance(data, dict) else {}
        (build_dir / "report.html").write_text(
            _render(findings, meta_for_report), encoding="utf-8")
        print("[enrich] report.html re-rendered with LLM narratives")
    except Exception as exc:
        print(f"[enrich] report re-render skipped: {str(exc)[:120]}")
    print(f"[enrich] {stats['accepted']}/{stats['calls']} narratives accepted "
          f"(rejected: {stats['rejected_numbers']} numbers, "
          f"{stats['rejected_wording']} wording; failed: {stats['failed']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(config.BUILD_DIR))
    args = ap.parse_args()
    return enrich(Path(args.build))


if __name__ == "__main__":
    sys.exit(main())
