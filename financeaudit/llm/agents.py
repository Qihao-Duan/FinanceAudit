"""AGENTS stage — the two PLAN §2.3 LLM agents, wired between DEFENSE and
VERDICT (user directive 2026-07-18: robustness).

    python3 -m financeaudit.llm.agents [--build build]

Role A · evidence-analysis agent: constrained scheme adjudication (closed
  10-scheme choice). A dissent is RECORDED (finding.llm_adjudication) for
  human review — it never reroutes the finding. Second-opinion robustness
  against misclassification (e.g. the F1 routing bug class).

Role B · defense agent: exculpatory-evidence hunt over entity-relevant rows
  from ALL ingested tables — including "orphan" tables that no deterministic
  rule consumes (mutation tests showed accrual_schedule / technical_assessments
  / batch_approvals were parsed but invisible to defense). May ONLY cite
  offered source_ids; exculpatory direction only; results are appended to
  counterevidence/innocence_checked ahead of the deterministic VERDICT gate.

Hard rules: no key -> no-op exit 0; every call has deterministic fallback
(skip + log); the LLM never computes amounts and never touches dispositions
directly. Everything is logged to <data_dir>/_agent_logs/ and version-stamped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from financeaudit.core import config
from financeaudit.llm.calllog import log_event
from financeaudit.llm.client import structured_call
from financeaudit.llm.prompts import (
    ADJUDICATION_PROMPT_VERSION, ADJUDICATION_SYSTEM, ADJUDICATION_SCHEMA,
    DEFENSE_HUNT_PROMPT_VERSION, DEFENSE_HUNT_SYSTEM, DEFENSE_HUNT_SCHEMA,
)

MAX_FINDINGS = int(__import__("os").environ.get("FA_AGENTS_MAX", "20"))
ROWS_PER_TABLE = 10
MAX_TABLES = 12


# --------------------------------------------------------------- selection

def _tier_rank(f):
    t = (f.get("risk_tier") or "low").lower()
    return {"high": 0, "medium": 1}.get(t, 2)


def select_findings(findings):
    """Highest-stakes first: tier, then amount. Cap at MAX_FINDINGS."""
    ranked = sorted(findings, key=lambda f: (_tier_rank(f),
                                             -float(f.get("amount_eur") or 0)))
    return ranked[:MAX_FINDINGS]


# --------------------------------------------------------------- payloads

def _finding_summary(f):
    return {
        "finding_id": f.get("finding_id"),
        "title": f.get("title"),
        "scheme": f.get("scheme"),
        "mechanism": f.get("mechanism"),
        "entity": f.get("entity_label"),
        "risk_tier": f.get("risk_tier"),
        "candidate_rules": sorted({c.split("-")[0] for c in f.get("candidate_ids", [])
                                    if isinstance(c, str)}),
        "claims": [{"role": c.get("role"), "type": c.get("type"),
                     "verdict": c.get("verdict"), "assertion": c.get("assertion")}
                    for c in f.get("claims", [])],
        "innocence_checked": f.get("innocence_checked", []),
        "counterevidence": f.get("counterevidence", []),
    }


def _entity_tokens(f):
    """Digit-bearing identifiers tied to this finding: accounts, doc refs,
    entry ids — mined from entity_key + claim assertions/citations."""
    toks = set()
    ek = str(f.get("entity_key") or "")
    if ":" in ek:
        toks.add(ek.split(":", 1)[1])
    blob = json.dumps(f.get("claims", []), ensure_ascii=False, default=str)
    toks |= set(re.findall(r"\b(?:AR|ER|GS|SG|WE|WA|AZ|GJ|GB|BE|IA-\d{4}-)\w+\b", blob))
    toks |= set(re.findall(r"\b77\d{5}\b", blob))
    toks |= set(re.findall(r"\b[12]\d{5}\b", blob))          # 6-digit accounts
    return {t for t in toks if len(t) >= 4}


def relevant_rows(con, f):
    """Entity-relevant rows from ALL DB tables (source_id-bearing), grouped by
    table — deliberately including tables no rule consumes."""
    toks = _entity_tokens(f)
    if not toks:
        return {}
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main'").fetchall()]
    skip = {"source_registry", "column_profiles", "source_manifest", "doc_units", "gl"}
    out = {}
    for t in tables:
        if t in skip or len(out) >= MAX_TABLES:
            continue
        cols = [r[0] for r in con.execute(f"DESCRIBE {t}").fetchall()]
        if "source_id" not in cols:
            continue
        text_cols = [c for c in cols if c != "source_id"]
        if not text_cols:
            continue
        concat = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR),'')"
                                     for c in text_cols)
        like = " OR ".join([f"({concat}) LIKE '%' || ? || '%'"] * len(toks))
        try:
            rows = con.execute(
                f"SELECT * FROM {t} WHERE {like} LIMIT {ROWS_PER_TABLE}",
                list(toks)).fetchall()
        except Exception:
            continue
        if rows:
            out[t] = [dict(zip(cols, r)) for r in rows]
    return out


# --------------------------------------------------------------- roles

def adjudicate(f):
    payload = _finding_summary(f)
    out = structured_call(
        config.OPENAI_MODEL_REASONING, ADJUDICATION_SYSTEM,
        json.dumps(payload, ensure_ascii=False, default=str),
        "scheme_adjudication", ADJUDICATION_SCHEMA,
        context={"finding_id": f.get("finding_id"), "role": "adjudication",
                 "prompt_version": ADJUDICATION_PROMPT_VERSION})
    if out is None:
        return False
    usage = out.pop("_usage", {})
    f["llm_adjudication"] = {
        "chosen_scheme": out["chosen_scheme"],
        "agrees": out["chosen_scheme"] == f.get("scheme"),
        "basis": out.get("basis", ""),
        "model": usage.get("model"),
        "prompt_version": ADJUDICATION_PROMPT_VERSION,
    }
    return True


def defense_hunt(con, f):
    rows = relevant_rows(con, f)
    if not rows:
        return False
    offered_sids = {r["source_id"] for rs in rows.values() for r in rs}
    payload = {"finding": _finding_summary(f), "tables": rows}
    out = structured_call(
        config.OPENAI_MODEL_REASONING, DEFENSE_HUNT_SYSTEM,
        json.dumps(payload, ensure_ascii=False, default=str),
        "defense_hunt", DEFENSE_HUNT_SCHEMA,
        context={"finding_id": f.get("finding_id"), "role": "defense_hunt",
                 "prompt_version": DEFENSE_HUNT_PROMPT_VERSION})
    if out is None:
        return False
    usage = out.pop("_usage", {})
    kept, dropped = [], 0
    for item in out.get("items", []):
        sids = [s for s in item.get("source_ids", []) if s in offered_sids]
        if not sids:
            dropped += 1
            continue
        kept.append({
            "predicate": f"llm_hunt:{item['evidence_kind']}",
            "question": f"LLM defense hunt over table '{item['table']}'",
            "result": "found",
            "source_ids": sids,
            "detail": item.get("why", ""),
            "llm": {"model": usage.get("model"),
                     "prompt_version": DEFENSE_HUNT_PROMPT_VERSION},
        })
    if dropped:
        log_event("filter_decision", {"finding_id": f.get("finding_id"),
                                       "decision": "hunt_items_dropped_bad_sids",
                                       "n": dropped})
    if kept:
        f.setdefault("counterevidence", []).extend(kept)
        f.setdefault("innocence_checked", []).extend(kept)
    f["llm_defense_hunt"] = {"n_tables_offered": len(rows),
                              "n_items": len(kept),
                              "prompt_version": DEFENSE_HUNT_PROMPT_VERSION}
    return True


# --------------------------------------------------------------- stage main

def run(build_dir: Path) -> int:
    src = build_dir / "findings_defended.json"
    if not src.exists():
        print("[agents] findings_defended.json missing — run defense first")
        return 1
    if not config.llm_available():
        print("[agents] OPENAI_API_KEY not set -> stage skipped (deterministic path)")
        return 0
    data = json.loads(src.read_text(encoding="utf-8"))
    findings = data["findings"] if isinstance(data, dict) else data
    import duckdb
    con = duckdb.connect(str(build_dir / "audit.duckdb"), read_only=True)

    sel = select_findings(findings)
    stats = {"selected": len(sel), "adjudicated": 0, "dissents": 0,
             "hunted": 0, "hunt_items": 0}
    for f in sel:
        if adjudicate(f):
            stats["adjudicated"] += 1
            if not f["llm_adjudication"]["agrees"]:
                stats["dissents"] += 1
        if defense_hunt(con, f):
            stats["hunted"] += 1
            stats["hunt_items"] += f.get("llm_defense_hunt", {}).get("n_items", 0)

    if isinstance(data, dict):
        data.setdefault("meta", {})["llm_agents"] = {
            "adjudication_version": ADJUDICATION_PROMPT_VERSION,
            "defense_hunt_version": DEFENSE_HUNT_PROMPT_VERSION,
            "model": config.OPENAI_MODEL_REASONING,
            **stats,
        }
    tmp = src.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    tmp.replace(src)
    print(f"[agents] {stats['adjudicated']}/{stats['selected']} adjudicated "
          f"({stats['dissents']} dissents), {stats['hunted']} hunted, "
          f"{stats['hunt_items']} exculpatory items added")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(config.BUILD_DIR))
    args = ap.parse_args()
    return run(Path(args.build))


if __name__ == "__main__":
    sys.exit(main())
