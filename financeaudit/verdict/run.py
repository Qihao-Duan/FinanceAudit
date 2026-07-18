"""Stage CLI: python3 -m financeaudit.verdict.run [--build build]

Core-claim gate + fraud-wording gate + top-12 cap over defended findings.
Outputs build/findings.json ({"meta":..., "findings":[...]} — UI and evalx
both accept the wrapper) and build/report.html (static evidence cards).
Rejected/quarantined findings stay in the file with their logs — nothing is
silently dropped, nothing is regenerated.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from financeaudit.evidence.common import connect, json_dump, json_load, now_iso
from financeaudit.verdict.gate import gate_all
from financeaudit.verdict import report_html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    ap.add_argument("--cap", type=int, default=12)
    args = ap.parse_args(argv)
    build = Path(args.build)

    con = connect(build)
    doc = json_load(build / "findings_defended.json")
    findings = doc["findings"]
    quarantined_from_claims = doc.get("quarantine", [])

    findings, stats = gate_all(con, findings, cap=args.cap)

    n_amount = sum(1 for f in findings for c in f["claims"]
                   if (c.get("recompute") or {}).get("applicable"))
    n_match = sum(1 for f in findings for c in f["claims"]
                  if (c.get("recompute") or {}).get("match") is True)
    meta = {
        "generated_at": now_iso(),
        "llm_used": False,
        "candidates_source": doc["meta"].get("candidates_source"),
        "fixture_supplemented": doc["meta"].get("fixture_supplemented", []),
        "n_findings": len(findings),
        "n_report": sum(1 for f in findings if f["disposition"] == "report"),
        "n_observation": sum(1 for f in findings if f["disposition"] == "observation"),
        "n_rejected": len(stats["rejected"]),
        "n_quarantined": len(stats["quarantined"]) + len(quarantined_from_claims),
        "n_amount_claims": n_amount,
        "n_amount_recompute_match": n_match,
        "report_cap": args.cap,
        "gate_stats": stats,
    }
    out = {"meta": meta, "findings": findings,
           "quarantine": quarantined_from_claims}
    json_dump(build / "findings.json", out)

    html = report_html.render(findings, meta)
    (build / "report.html").write_text(html, encoding="utf-8")

    print(f"[verdict] wrote {build/'findings.json'} + {build/'report.html'}: "
          f"{meta['n_report']} report, {meta['n_observation']} observation, "
          f"{meta['n_rejected']} rejected, {meta['n_quarantined']} quarantined; "
          f"amount recompute {n_match}/{n_amount} exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
