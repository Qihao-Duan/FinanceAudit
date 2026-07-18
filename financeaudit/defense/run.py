"""Stage CLI: python3 -m financeaudit.defense.run [--build build]

Independent defense pass (PLAN §3.5): runs the innocence-predicate checklist
for each finding's scheme as deterministic lookups, annotates
innocence_checked + counterevidence in place, and never argues from tone or
prestige. Exculpatory 'found' results become counterevidence entries; a
direct contradiction of a core claim flips that claim's verdict (logged).
Output: build/findings_defended.json. llm_used: false (no OPENAI_API_KEY —
the deterministic path is authoritative; an LLM would only phrase narrative).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from financeaudit.evidence.common import connect, json_dump, json_load, now_iso
from financeaudit.defense.predicates import run_predicates

#: predicate -> claim types it can contradict when result == 'found'
_CONTRADICTS = {
    "contract_ref_resolvable": "existence",       # cited doc actually resolves
    "entity_identity_consistent": "entity_attribute",
    "batch_payment_authorization": None,          # mitigates, does not contradict
}


def defend_finding(con, finding, pack) -> None:
    preds = run_predicates(con, finding, pack)
    finding["innocence_checked"] = preds

    counter = []
    for p in preds:
        if p["result"] == "found" and p.get("exculpatory", True):
            counter.append({
                "kind": p["predicate"],
                "description": f"{p['question']} — evidence found.",
                "source_ids": p.get("source_ids", []),
                "detail": p.get("detail"),
            })
    # probe-level counterevidence from the evidence pack (approvals etc.)
    probe = pack.get("counterevidence_probe", {})
    for a in probe.get("approvals", []):
        counter.append({"kind": "journal_approval_log",
                        "description": f"Journal {a.get('journal_name')} (entry "
                                       f"{a.get('entry_id')}) was approved by "
                                       f"{a.get('approver')} (creator {a.get('creator')}).",
                        "source_ids": [a.get("source_id")]})
    # dedupe by (kind, first source_id)
    seen, deduped = set(), []
    for c in counter:
        key = (c["kind"], (c["source_ids"] or [None])[0])
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    finding["counterevidence"] = deduped

    # direct contradictions flip claim verdicts (logged, never silently)
    log = []
    for p in preds:
        ctype = _CONTRADICTS.get(p["predicate"])
        if ctype and p["result"] == "found":
            for c in finding["claims"]:
                if c["type"] == ctype and c["role"] == "core" and c["verdict"] == "supported":
                    c["verdict"] = "contradicted"
                    c["defense_note"] = (f"contradicted by defense predicate "
                                         f"{p['predicate']}: evidence found")
                    log.append({"claim_id": c["claim_id"], "predicate": p["predicate"]})
    finding["defense_log"] = {
        "n_predicates": len(preds),
        "n_found": sum(1 for p in preds if p["result"] == "found"),
        "n_not_found": sum(1 for p in preds if p["result"] == "not_found"),
        "n_not_checkable": sum(1 for p in preds if p["result"] == "not_checkable"),
        "verdicts_flipped": log,
        "statement": ("counterevidence found — see counterevidence list"
                      if deduped else
                      "checked all innocence predicates for this scheme; "
                      "no counterevidence found"),
        "llm_used": False,
    }
    # PBC requests for not_checkable predicates
    for p in preds:
        if p["result"] == "not_checkable" and p.get("reason"):
            req = f"[defense] material to resolve '{p['predicate']}': {p['reason']}"
            if req not in finding["pbc_requests"]:
                finding["pbc_requests"].append(req)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    args = ap.parse_args(argv)
    build = Path(args.build)

    con = connect(build)
    draft = json_load(build / "findings_draft.json")
    packs = {p["pack_id"]: p for p in json_load(build / "evidence_packs.json")["packs"]}

    for f in draft["findings"]:
        pack = packs.get(f.get("pack_id"), {})
        defend_finding(con, f, pack)

    draft["meta"]["defense_generated_at"] = now_iso()
    draft["meta"]["defense_llm_used"] = False
    json_dump(build / "findings_defended.json", draft)
    n = len(draft["findings"])
    npred = sum(len(f["innocence_checked"]) for f in draft["findings"])
    nctr = sum(len(f["counterevidence"]) for f in draft["findings"])
    nflip = sum(len(f["defense_log"]["verdicts_flipped"]) for f in draft["findings"])
    print(f"[defense] wrote {build/'findings_defended.json'}: {n} findings, "
          f"{npred} predicates checked, {nctr} counterevidence entries, "
          f"{nflip} claim verdicts flipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
