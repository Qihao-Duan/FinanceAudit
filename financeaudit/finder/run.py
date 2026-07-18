"""Finder stage runner — executes all rules_*.py modules -> build/candidates.json.

CLI (CONTRACTS §1):  python3 -m financeaudit.finder.run [--build build]

- (Re)extracts thresholds into build/thresholds.json first (idempotent).
- Dynamically imports every financeaudit/finder/rules_*.py present
  (rules_control.py owned by Agent B; rules_statistical.py / rules_relational.py
  owned by Agent C — imported automatically once they exist).
- Each module exposes RULES = [{"rule_id": ..., "fn": fn(con, thresholds)}].
- Candidates are concatenated (mark-and-cluster: NO cross-rule dedupe),
  candidate_ids assigned per rule as '<rule_id>-<seq:04d>'.
- A crashing rule is recorded and skipped; the runner exits 1 at the end if
  any rule crashed (code bug must surface), otherwise 0.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import duckdb

from financeaudit.finder import thresholds as thresholds_mod

REQUIRED_KEYS = (
    "rule_id", "rule_name", "channel", "anomaly_type", "risk_tier",
    "entity_key", "entity_label", "entry_ids", "source_ids", "metrics",
    "denominators", "description",
)
DENOM_KEYS = ("population_size", "rule_hits", "peers_with_expected_evidence")


def discover_rule_modules() -> List[str]:
    pkg_dir = Path(__file__).resolve().parent
    names = sorted(p.stem for p in pkg_dir.glob("rules_*.py"))
    return [f"financeaudit.finder.{n}" for n in names]


def validate_candidate(cand: Dict[str, Any], rule_id: str) -> List[str]:
    problems = []
    for k in REQUIRED_KEYS:
        if k not in cand:
            problems.append(f"missing key '{k}'")
    if cand.get("rule_id") != rule_id:
        problems.append(f"rule_id mismatch: {cand.get('rule_id')!r} != {rule_id!r}")
    if not cand.get("source_ids"):
        problems.append("empty source_ids (every candidate must cite rows)")
    denoms = cand.get("denominators") or {}
    for k in DENOM_KEYS:
        if k not in denoms:
            problems.append(f"denominators missing '{k}'")
    if cand.get("risk_tier") not in ("high", "medium", "low"):
        problems.append(f"bad risk_tier {cand.get('risk_tier')!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Finder: run rule modules -> candidates.json")
    ap.add_argument("--build", default="build")
    args = ap.parse_args()
    build_dir = Path(args.build)
    db_path = build_dir / "audit.duckdb"
    if not db_path.exists():
        print(f"ERROR: {db_path} not found — run `python3 -m financeaudit.ingest.run` first",
              file=sys.stderr)
        return 1

    con = duckdb.connect(str(db_path), read_only=True)

    # 1. thresholds (always re-extracted; cheap + idempotent)
    thresholds = thresholds_mod.build_thresholds(con, build_dir)
    print(f"[finder] thresholds.json written "
          f"({sum(1 for t in thresholds['thresholds'].values() if t['provenance']=='extracted')}"
          f"/{len(thresholds['thresholds'])} extracted from documents)")

    # 2. dynamic rule discovery + execution
    candidates: List[Dict[str, Any]] = []
    per_rule_counts: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []
    corroboration_flags: List[Dict[str, Any]] = []

    for mod_name in discover_rule_modules():
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            errors.append({"module": mod_name, "error": traceback.format_exc()})
            print(f"[finder] ERROR importing {mod_name}", file=sys.stderr)
            continue
        rules = getattr(mod, "RULES", None)
        if not rules:
            print(f"[finder] WARNING: {mod_name} has no RULES list — skipped",
                  file=sys.stderr)
            continue
        for rule in rules:
            rule_id, fn = rule["rule_id"], rule["fn"]
            try:
                out = fn(con, thresholds) or []
            except Exception:
                errors.append({"module": mod_name, "rule_id": rule_id,
                               "error": traceback.format_exc()})
                print(f"[finder] ERROR in {rule_id} ({mod_name}):", file=sys.stderr)
                traceback.print_exc()
                per_rule_counts[rule_id] = per_rule_counts.get(rule_id, 0)
                continue
            if rule.get("corroboration_only"):
                # CONTRACTS §3: corroboration flags (R13 Benford) never become
                # standalone candidates — they are recorded in finder_meta only
                # and joined to same-entity candidates via their metrics.
                corroboration_flags.extend(out)
                per_rule_counts[rule_id] = 0
                continue
            seq = 0
            for cand in out:
                problems = validate_candidate(cand, rule_id)
                if problems:
                    errors.append({"module": mod_name, "rule_id": rule_id,
                                   "error": "schema: " + "; ".join(problems)})
                    print(f"[finder] SCHEMA problem in {rule_id}: {problems}",
                          file=sys.stderr)
                    continue
                seq += 1
                cand["candidate_id"] = f"{rule_id}-{seq:04d}"
                candidates.append(cand)
            per_rule_counts[rule_id] = per_rule_counts.get(rule_id, 0) + seq

    # candidate_id first in serialized dicts (cosmetic, stable ordering)
    ordered = [{"candidate_id": c["candidate_id"],
                **{k: c[k] for k in c if k != "candidate_id"}} for c in candidates]

    out_path = build_dir / "candidates.json"
    out_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "llm_used": False,
        "n_candidates": len(ordered),
        "per_rule_counts": per_rule_counts,
        "corroboration_flags": corroboration_flags,
        "errors": errors,
    }
    (build_dir / "finder_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[finder] wrote {out_path} ({len(ordered)} candidates)")
    print("[finder] per-rule hit counts:")
    for rid in sorted(per_rule_counts):
        print(f"  {rid}: {per_rule_counts[rid]}")
    if errors:
        print(f"[finder] {len(errors)} rule error(s) — see build/finder_meta.json",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
