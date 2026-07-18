#!/usr/bin/env python3
"""FinanceAudit UI smoke test.

Pulls /api/status, /api/manifest, /api/findings and EVERY /api/findings/{id},
then sanity-checks the exact fields the frontend templates consume so a missing
or wrong-typed key can never render as `undefined` (or throw) in a card.

No third-party deps. Run against a live server:

    ./scripts/serve_ui.sh &            # port 8642
    python3 ui/smoke.py                # or: python3 ui/smoke.py http://127.0.0.1:8642

Exit code 0 = all green, 1 = at least one ERROR.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        os.environ.get("FA_UI_URL", "http://127.0.0.1:8642")).rstrip("/")

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


def get(path: str):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:                              # noqa: BLE001
        err(f"{path}: request failed: {e}")
        return None, None


def is_str(v) -> bool:
    return isinstance(v, str) and v != ""


def is_num_or_none(v) -> bool:
    return v is None or isinstance(v, (int, float)) and not isinstance(v, bool)


# --------------------------------------------------------------------------
def check_citation(fid: str, cid: str, i: int, cit) -> None:
    where = f"{fid}/{cid}/cite[{i}]"
    if not isinstance(cit, dict):
        err(f"{where}: citation is not an object")
        return
    if not is_str(cit.get("source_id")):
        err(f"{where}: missing source_id")
    if not is_str(cit.get("file")):
        warn(f"{where}: missing file (chip label falls back to '')")
    kind = cit.get("kind")
    if kind == "cell":
        if not isinstance(cit.get("row_ids", []), list):
            err(f"{where}: row_ids not a list")
    # page citations: page_label / page_index consumed with fallbacks -> tolerant


def check_claim(fid: str, c) -> None:
    cid = c.get("claim_id", "?")
    where = f"{fid}/{cid}"
    if not isinstance(c, dict):
        err(f"{fid}: claim is not an object")
        return
    for key in ("role", "type", "verdict", "assertion", "claim_id"):
        if key not in c:
            err(f"{where}: claim missing '{key}'")
    if not isinstance(c.get("citations", []), list):
        err(f"{where}: citations is not a list")
    val = c.get("value")
    if val is not None and not isinstance(val, dict):
        err(f"{where}: value present but not an object")
    if isinstance(val, dict) and val.get("formula") is not None \
            and not is_str(val.get("formula")):
        err(f"{where}: value.formula present but not a string")
    for i, cit in enumerate(c.get("citations", []) or []):
        check_citation(fid, cid, i, cit)


def check_finding(fid: str, f) -> None:
    if not isinstance(f, dict):
        err(f"{fid}: detail is not an object")
        return
    # scalar text fields consumed directly
    for key in ("finding_id", "title", "disposition"):
        if not is_str(f.get(key)):
            err(f"{fid}: missing/empty '{key}'")
    if not is_num_or_none(f.get("amount_eur")):
        err(f"{fid}: amount_eur must be number or null, got {type(f.get('amount_eur')).__name__}")
    # list containers the card iterates
    for key in ("claims", "innocence_checked", "counterevidence",
                "evidence_status", "expected_evidence", "missing_evidence",
                "pbc_requests"):
        v = f.get(key, [])
        if v is not None and not isinstance(v, list):
            err(f"{fid}: '{key}' must be a list, got {type(v).__name__}")
    dn = f.get("denominators", {})
    if dn is not None and not isinstance(dn, dict):
        err(f"{fid}: denominators must be an object")
    # evidence_status entry shape
    for i, e in enumerate(f.get("evidence_status", []) or []):
        if not isinstance(e, dict) or "item" not in e or "state" not in e:
            err(f"{fid}: evidence_status[{i}] needs 'item' and 'state'")
    # claims
    for c in f.get("claims", []) or []:
        check_claim(fid, c)


# --------------------------------------------------------------------------
def main() -> int:
    print(f"FinanceAudit UI smoke — base {BASE}")

    st, status = get("/api/status")
    print(f"  GET /api/status            -> {st}")
    if st != 200 or not isinstance(status, dict):
        err("/api/status did not return 200/JSON")
    else:
        if not status.get("ok"):
            err("/api/status ok != true")
        print(f"      mode={status.get('mode')} n_findings={status.get('n_findings')}")

    mt, manifest = get("/api/manifest")
    print(f"  GET /api/manifest          -> {mt}")
    summary = {}
    if mt != 200 or not isinstance(manifest, dict):
        err("/api/manifest did not return 200/JSON")
    else:
        summary = manifest.get("summary") or {}
        for key in ("report_count", "observation_count", "quarantine_count",
                    "flagged_amount_total", "citations"):
            if key not in summary:
                err(f"/api/manifest summary missing '{key}'")
        cit = summary.get("citations") or {}
        print(f"      report={summary.get('report_count')} "
              f"obs={summary.get('observation_count')} "
              f"quarantine={summary.get('quarantine_count')} "
              f"flagged={summary.get('flagged_amount_total')} "
              f"citations={cit.get('resolvability_pct')}% "
              f"({cit.get('resolved')}/{cit.get('total')})")

    lt, listing = get("/api/findings")
    print(f"  GET /api/findings          -> {lt}")
    if lt != 200 or not isinstance(listing, list):
        err("/api/findings did not return 200/list")
        _report()
        return 1

    # list-item fields consumed by the left pane
    for it in listing:
        fid = it.get("finding_id")
        if not is_str(fid):
            err("list item missing finding_id")
            continue
        for key in ("title", "disposition", "scheme"):
            if key not in it:
                err(f"list/{fid}: missing '{key}'")
        if "rank" not in it:
            err(f"list/{fid}: missing 'rank' (needed for KEY FINDINGS numbering)")
        if not is_num_or_none(it.get("amount_eur")):
            err(f"list/{fid}: amount_eur not number/null")

    n_report = sum(1 for it in listing if it.get("disposition") == "report")
    n_obs = sum(1 for it in listing if it.get("disposition") == "observation")
    flagged = sum(it.get("amount_eur") or 0
                  for it in listing if it.get("disposition") == "report")
    print(f"      {len(listing)} findings: {n_report} report / {n_obs} observation")
    print(f"      flagged amount total (list sum): {flagged:,.0f} EUR")

    # cross-check exec-strip numbers against the manifest summary
    if summary:
        if summary.get("report_count") != n_report:
            warn(f"summary.report_count {summary.get('report_count')} != list {n_report}")
        if summary.get("flagged_amount_total") not in (None,) and \
                abs((summary.get('flagged_amount_total') or 0) - flagged) > 0.005:
            warn(f"summary.flagged_amount_total {summary.get('flagged_amount_total')} "
                 f"!= list sum {flagged}")

    # every detail endpoint
    ok = 0
    for it in listing:
        fid = it.get("finding_id")
        if not is_str(fid):
            continue
        dt, detail = get(f"/api/findings/{fid}")
        if dt != 200 or detail is None:
            err(f"/api/findings/{fid} -> {dt}")
            continue
        check_finding(fid, detail)
        ok += 1
    print(f"  GET /api/findings/{{id}}     -> {ok}/{len(listing)} fetched (200)")

    _report()
    return 1 if errors else 0


def _report() -> None:
    print()
    if warns:
        print(f"WARNINGS ({len(warns)}):")
        for w in warns:
            print(f"  ! {w}")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  x {e}")
        print("\nSMOKE: FAIL")
    else:
        print("SMOKE: PASS — all consumed fields present and well-typed.")


if __name__ == "__main__":
    sys.exit(main())
