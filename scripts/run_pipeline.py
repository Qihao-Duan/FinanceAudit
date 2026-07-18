#!/usr/bin/env python3
"""FinanceAudit — full pipeline runner (integrator glue, CONTRACTS §1).

Runs all stages in contract order with clear banners and per-stage timing.
Stops immediately when a stage exits nonzero (A-class property-test failure
or unresolvable-citation eval failure); B-class deviations never exit nonzero
by contract and therefore never stop the pipeline.

Usage:
    python3 scripts/run_pipeline.py [--data data/practice] [--build build]
                                    [--skip-eval]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STAGES = [
    ("INGEST", "financeaudit.ingest.run", ["--data", "{data}", "--out", "{build}"]),
    ("FINDER", "financeaudit.finder.run", ["--build", "{build}"]),
    ("EVIDENCE", "financeaudit.evidence.run", ["--build", "{build}"]),
    ("CLAIMS", "financeaudit.claims.run", ["--build", "{build}"]),
    ("DEFENSE", "financeaudit.defense.run", ["--build", "{build}"]),
    ("AGENTS", "financeaudit.llm.agents", ["--build", "{build}"]),
    ("VERDICT", "financeaudit.verdict.run", ["--build", "{build}"]),
    ("ENRICH", "financeaudit.llm.enrich", ["--build", "{build}"]),
    ("EVAL", "evalx.run", ["--build", "{build}"]),
]


def banner(name: str, extra: str = "") -> None:
    line = "=" * 78
    print(f"\n{line}\n== STAGE {name:<10} {extra}\n{line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Honor the same env vars the stage modules use (README documents them) —
    # explicit flags still win. Prevents the "env set but orchestrator ran
    # data/practice anyway" footgun (finals run 2026-07-18).
    ap.add_argument("--data", default=os.environ.get("FA_DATA_DIR", "data/practice"))
    ap.add_argument("--build", default=os.environ.get("FA_BUILD_DIR", "build"))
    ap.add_argument("--skip-eval", action="store_true",
                    help="run the six pipeline stages but not evalx")
    args = ap.parse_args()

    subst = {"data": args.data, "build": args.build}
    timings = []
    for name, module, argv in STAGES:
        if args.skip_eval and module == "evalx.run":
            continue
        cmd = [sys.executable, "-m", module] + [a.format(**subst) for a in argv]
        banner(name, " ".join(cmd[1:]))
        t0 = time.perf_counter()
        env = dict(os.environ, FA_STAGE=name)
        rc = subprocess.call(cmd, cwd=str(REPO), env=env)
        dt = time.perf_counter() - t0
        timings.append((name, dt, rc))
        print(f"-- {name} finished in {dt:.2f}s (exit {rc})", flush=True)
        if rc != 0:
            print(f"\nPIPELINE STOPPED: stage {name} exited {rc} "
                  f"(A-class property-test failure or eval gate) — fix the root "
                  f"cause; B-class deviations never stop the pipeline.",
                  file=sys.stderr)
            return rc

    banner("SUMMARY")
    total = sum(dt for _, dt, _ in timings)
    for name, dt, rc in timings:
        print(f"  {name:<10} {dt:8.2f}s  exit {rc}")
    print(f"  {'TOTAL':<10} {total:8.2f}s")
    print(f"\nOutputs in {args.build}/: findings.json, report.html, "
          f"eval_report.json\nUI: uvicorn ui.server:app --port 8642")
    return 0


if __name__ == "__main__":
    sys.exit(main())
