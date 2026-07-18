"""Stage CLI: python3 -m financeaudit.claims.run [--build build]

Reads build/evidence_packs.json, emits build/findings_draft.json.
Builder exceptions never kill the stage: the affected pack yields a quarantine
stub with the error log preserved (no silent drops, no auto-regeneration).
"""
from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from financeaudit.evidence.common import connect, json_dump, json_load, now_iso
from financeaudit.claims.engine import build_finding


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    args = ap.parse_args(argv)
    build = Path(args.build)

    con = connect(build)
    packs_doc = json_load(build / "evidence_packs.json")
    packs = packs_doc["packs"]

    findings, quarantined = [], []
    for i, pack in enumerate(packs, start=1):
        try:
            findings.append(build_finding(con, pack, i))
        except Exception:
            quarantined.append({
                "finding_id": f"F-{i:04d}",
                "pack_id": pack.get("pack_id"),
                "entity_key": pack.get("entity_key"),
                "disposition": "quarantine",
                "error_log": traceback.format_exc(),
                "stage": "claims",
            })

    n_claims = sum(len(f["claims"]) for f in findings)
    n_amount = sum(1 for f in findings for c in f["claims"]
                   if (c.get("value") or {}).get("kind") == "amount")
    n_match = sum(1 for f in findings for c in f["claims"]
                  if (c.get("recompute") or {}).get("match") is True)
    out = {
        "meta": {"generated_at": now_iso(), "llm_used": False,
                 "candidates_source": packs_doc["meta"].get("candidates_source"),
                 "fixture_supplemented": packs_doc["meta"].get("fixture_supplemented", []),
                 "n_findings": len(findings), "n_quarantined": len(quarantined),
                 "n_claims": n_claims, "n_amount_claims": n_amount,
                 "n_amount_recompute_match": n_match},
        "findings": findings,
        "quarantine": quarantined,
    }
    json_dump(build / "findings_draft.json", out)
    print(f"[claims] wrote {build/'findings_draft.json'}: {len(findings)} drafts, "
          f"{n_claims} claims, amount recompute {n_match}/{n_amount} exact, "
          f"{len(quarantined)} quarantined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
