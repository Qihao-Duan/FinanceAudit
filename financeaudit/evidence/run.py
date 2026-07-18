"""Stage CLI: python3 -m financeaudit.evidence.run [--build build]

Reads build/candidates.json (Agents B+C). If absent, generates
build/candidates.fixture.json live from audit.duckdb and runs on that,
flagging candidates_source='fixture' in the output meta.
Writes build/evidence_packs.json. Deterministic; llm_used: false.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from financeaudit.evidence.common import (
    connect, json_dump, json_load, load_thresholds, now_iso,
)
from financeaudit.evidence.fixture_candidates import build_fixture_candidates
from financeaudit.evidence.packs import build_packs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    args = ap.parse_args(argv)
    build = Path(args.build)

    con = connect(build)
    cand_path = build / "candidates.json"
    fixture_path = build / "candidates.fixture.json"
    supplemented: list[str] = []
    if cand_path.exists():
        data = json_load(cand_path)
        candidates = data["candidates"] if isinstance(data, dict) and "candidates" in data else data
        source = "pipeline"
        # Agent C's relational rules (R15/R16/R17) may not have shipped yet.
        # If an entire rule family is absent, supplement it from the fixture
        # generator so downstream stages stay testable — clearly flagged.
        present_rules = {c.get("rule_id") for c in candidates}
        fixture = build_fixture_candidates(con)
        for rule in ("R15", "R16", "R17"):
            if rule not in present_rules:
                for fc in fixture:
                    if fc["rule_id"] == rule:
                        fc = dict(fc)
                        fc["candidate_id"] = fc["candidate_id"].replace(
                            f"{rule}-", f"{rule}-FIXT-")
                        fc["fixture_supplement"] = True
                        candidates.append(fc)
                        supplemented.append(fc["candidate_id"])
        if supplemented:
            source = "pipeline+fixture_supplement"
            json_dump(fixture_path, fixture)
            print(f"[evidence] NOTE: supplemented missing rule families from "
                  f"fixture: {supplemented}", file=sys.stderr)
    else:
        candidates = build_fixture_candidates(con)
        json_dump(fixture_path, candidates)
        source = "fixture"
        print(f"[evidence] WARNING: {cand_path} missing — generated and used "
              f"{fixture_path} ({len(candidates)} candidates)", file=sys.stderr)

    thresholds = load_thresholds(build, con)
    packs = build_packs(con, candidates, thresholds)
    out = {
        "meta": {
            "generated_at": now_iso(),
            "llm_used": False,
            "candidates_source": source,
            "fixture_supplemented": supplemented,
            "n_candidates": len(candidates),
            "n_packs": len(packs),
            "thresholds_origin": thresholds.get("origin"),
        },
        "packs": packs,
    }
    json_dump(build / "evidence_packs.json", out)
    print(f"[evidence] wrote {build/'evidence_packs.json'}: {len(packs)} packs "
          f"from {len(candidates)} candidates (source={source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
