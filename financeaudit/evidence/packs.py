"""Evidence pack builder (Agent D, stage 2 — PLAN §3.3).

Clusters candidates by entity_key and assembles BIDIRECTIONAL packs:
incriminating rows + entity master data + obligation gaps + conflicts +
denominators, PLUS potential counterevidence for the same entity (reversals,
credit notes, approvals, accruals, normal history, peers) so the claims stage
never sees a one-sided story. Purely deterministic (llm_used: false).
"""
from __future__ import annotations

from collections import defaultdict

from financeaudit.evidence.common import (
    absence_gate, load_manifest, q, registry_lookup, scan_token, resolves_outside,
)

_ROW_TABLES = [
    "gl", "vendor_tx", "customer_tx", "asset_tx", "assets", "vendors", "customers",
    "goods_receipts", "goods_issues", "sales_invoices", "purchase_invoices_2026",
    "subsequent_payments", "approval_log", "masterdata_changes", "permissions",
    "shareholders", "trial_balance",
]


def _rows_by_sid(con, sids: list[str]) -> dict[str, list[dict]]:
    """Resolve candidate source_ids back to their table rows."""
    out = defaultdict(list)
    sids = [s for s in set(sids) if s]
    if not sids:
        return out
    ph = ",".join("?" * len(sids))
    for t in _ROW_TABLES:
        try:
            rows = q(con, f"SELECT * FROM {t} WHERE source_id IN ({ph})", sids)
        except Exception:
            continue
        out[t].extend(rows)
    return out


def _vendor_account(entity_key: str) -> str | None:
    if entity_key.startswith("vendor:"):
        return entity_key.split(":", 1)[1]
    return None


def _normal_history(con, account: str, before: str | None) -> dict:
    where = "account = ?"
    params = [account]
    if before:
        where += " AND posting_date < ?"
        params.append(before)
    r = q(con, f"SELECT count(*) AS n, min(posting_date) AS first_tx, "
               f"max(posting_date) AS last_tx, sum(amount) AS net "
               f"FROM vendor_tx WHERE {where}", params)[0]
    return {"n_tx": r["n"], "first_tx": str(r["first_tx"]), "last_tx": str(r["last_tx"]),
            "net_sum": float(r["net"]) if r["net"] is not None else 0.0,
            "window": f"before {before}" if before else "full year"}


def _approvals_for_entries(con, entry_ids: list[str]) -> list[dict]:
    if not entry_ids:
        return []
    ph = ",".join("?" * len(entry_ids))
    return q(con, f"SELECT * FROM approval_log WHERE entry_id IN ({ph})", list(entry_ids))


def _counterevidence_probe(con, entity_key: str, rows: dict, entry_ids: list[str]) -> dict:
    """Bidirectional probe: what could exonerate this entity?"""
    acct = _vendor_account(entity_key)
    probe = {"reversals": [], "credit_notes": [], "approvals": [], "accruals": [],
             "normal_history": None, "goods_receipts": [], "asset_cards": []}
    probe["approvals"] = _approvals_for_entries(con, entry_ids)
    if acct:
        first_flag = min((str(r["posting_date"]) for r in rows.get("gl", [])
                          if r.get("posting_date")), default=None)
        probe["normal_history"] = _normal_history(con, acct, first_flag)
        probe["reversals"] = q(con, "SELECT * FROM gl WHERE sub_account = ? AND "
                                    "(lower(text) LIKE '%storno%' OR lower(text) LIKE '%gutschrift%')",
                               [acct])
        probe["credit_notes"] = q(con, "SELECT * FROM vendor_tx WHERE account = ? AND "
                                       "(lower(text) LIKE '%gutschrift%' OR lower(text) LIKE '%storno%')",
                                  [acct])
        probe["goods_receipts"] = q(con, "SELECT * FROM goods_receipts WHERE vendor_account = ?",
                                    [acct])
    # accrual counterevidence: any year-end accrual rows already collected, plus
    # a dossier-wide probe for accrual texts touching the same context
    probe["accruals"] = q(con, "SELECT * FROM gl WHERE lower(text) LIKE '%rückstellung%' "
                               "AND posting_date >= '2025-12-01'")
    # asset cards for any asset-account rows in the pack (exculpatory for
    # capitalization questions: card exists + active)
    asset_ids = sorted({r["account"] for r in rows.get("gl", [])
                        if r.get("account", "").startswith(("04", "06")) and "-" in r.get("account", "")})
    if asset_ids:
        ph = ",".join("?" * len(asset_ids))
        probe["asset_cards"] = q(con, f"SELECT * FROM assets WHERE asset_id IN ({ph})", asset_ids)
    return probe


def _peer_stats(con, entity_key: str) -> dict:
    peers = {}
    if entity_key.startswith("vendor:"):
        nv = q(con, "SELECT count(*) AS n FROM masterdata_changes WHERE kind='Kreditor' "
                    "AND field LIKE 'Neuanlage%'")[0]["n"]
        ni = q(con, "SELECT count(*) AS n FROM masterdata_changes WHERE kind='Kreditor' "
                    "AND field LIKE 'Neuanlage%' AND changed_by <> approved_by")[0]["n"]
        peers["new_vendors_2025"] = nv
        peers["new_vendors_independently_approved"] = ni
        peers["vendors_total"] = q(con, "SELECT count(*) AS n FROM vendors")[0]["n"]
    peers["gl_rows_total"] = q(con, "SELECT count(*) AS n FROM gl")[0]["n"]
    peers["gl_entries_total"] = q(con, "SELECT count(DISTINCT entry_id) AS n FROM gl")[0]["n"]
    return peers


def _obligation_gaps(con, manifest: dict, entity_key: str, rows: dict) -> list[dict]:
    """Typed evidence-obligation gaps for the transactions in this pack."""
    gaps = []
    gl_rows = rows.get("gl", [])
    texts = " ".join((r.get("text") or "").lower() for r in gl_rows)
    is_service = any(w in texts for w in ("beratung", "leistung", "umlage"))
    acct = _vendor_account(entity_key)
    if acct and gl_rows:
        gr = q(con, "SELECT count(*) AS n FROM goods_receipts WHERE vendor_account=?", [acct])[0]["n"]
        etype = "goods_receipt_services" if is_service else "goods_receipt_material"
        gate = absence_gate(manifest, etype)
        gaps.append({"evidence_type": etype, "obligation_level":
                     "K2 audit-plan criterion" if not is_service else "business-type rule",
                     "observed": gr, "status": "observed" if gr else gate["claim_type"],
                     "wording": gate["qualifier"], "coverage": gate["coverage"]})
    if "rahmenvertrag" in texts or "vertrag" in texts:
        gate = absence_gate(manifest, "contract_document")
        hits = scan_token(con, "Rahmenvertrag")
        gaps.append({"evidence_type": "contract_document",
                     "obligation_level": "business-type rule (services)",
                     "observed": 1 if resolves_outside(hits) else 0,
                     "status": "observed" if resolves_outside(hits) else gate["claim_type"],
                     "wording": gate["qualifier"], "coverage": gate["coverage"],
                     "scan_hits": [h for h in hits if h["source_id"]][:10]})
    if any((r.get("posting_type") == "Zahlung") for r in gl_rows):
        gate = absence_gate(manifest, "bank_statement")
        gaps.append({"evidence_type": "bank_statement", "obligation_level": "PBC only",
                     "observed": 0, "status": gate["claim_type"],
                     "wording": "bank statements are not part of the dossier scope — "
                                "PBC request only, never a missing-control assertion",
                     "coverage": gate["coverage"]})
    return gaps


def _conflicts(con, entity_key: str, rows: dict) -> list[dict]:
    """Cross-document identity conflicts touching this entity."""
    out = []
    acct = _vendor_account(entity_key)
    if not acct:
        return out
    vm = q(con, "SELECT * FROM vendors WHERE account=?", [acct])
    sh = q(con, "SELECT * FROM shareholders WHERE note LIKE ? OR relation LIKE ?",
           [f"%{acct}%", f"%{acct}%"])
    for v in vm:
        for s in sh:
            if s.get("is_section_header"):
                continue
            if (s.get("name") or "").split("(")[0].strip() not in (v.get("name") or ""):
                out.append({
                    "kind": "entity_identity",
                    "field": f"holder of vendor account {acct}",
                    "a": {"file_role": "vendor master", "value": v.get("name"),
                          "source_id": v["source_id"]},
                    "b": {"file_role": "shareholder list", "value": s.get("name"),
                          "note": s.get("note"), "source_id": s["source_id"]},
                })
    return out


def build_packs(con, candidates: list[dict], thresholds: dict) -> list[dict]:
    manifest = load_manifest(con)
    clusters: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        clusters[c.get("entity_key") or f"candidate:{c['candidate_id']}"].append(c)

    packs = []
    for entity_key in sorted(clusters):
        cands = clusters[entity_key]
        all_sids = sorted({s for c in cands for s in c.get("source_ids", [])})
        entry_ids = sorted({e for c in cands for e in c.get("entry_ids", [])})
        rows = _rows_by_sid(con, all_sids)
        # pull complete GL entries for every referenced entry_id (balancing context)
        if entry_ids:
            ph = ",".join("?" * len(entry_ids))
            rows["gl_full_entries"] = q(
                con, f"SELECT * FROM gl WHERE entry_id IN ({ph}) ORDER BY entry_id, line_no",
                entry_ids)
        # entity master
        master = {}
        acct = _vendor_account(entity_key)
        if acct:
            vm = q(con, "SELECT * FROM vendors WHERE account=?", [acct])
            if vm:
                master = vm[0]
        # denominators: element-wise max across candidates
        denom = {}
        for c in cands:
            for k, v in (c.get("denominators") or {}).items():
                denom[k] = max(denom.get(k, 0), v or 0)
        users = sorted({r.get("user_id") for r in rows.get("gl", []) if r.get("user_id")})
        user_perms = []
        if users:
            ph = ",".join("?" * len(users))
            user_perms = q(con, f"SELECT * FROM permissions WHERE user_id IN ({ph})", users)
        registry = registry_lookup(con, all_sids)
        coverage = "complete" if all(
            m.get("parse_coverage") == "complete"
            for m in (manifest.get(r["file"]) for r in registry.values()) if m) else "partial"

        packs.append({
            "pack_id": f"P-{entity_key.replace(':', '-')}",
            "entity_key": entity_key,
            "entity_label": cands[0].get("entity_label") or entity_key,
            "candidates": cands,
            "entry_ids": entry_ids,
            "rows": {k: v for k, v in rows.items() if v},
            "entity_master": master,
            "user_permissions": user_perms,
            "obligation_gaps": _obligation_gaps(con, manifest, entity_key, rows),
            "conflicts": _conflicts(con, entity_key, rows),
            "counterevidence_probe": _counterevidence_probe(con, entity_key, rows, entry_ids),
            "peers": _peer_stats(con, entity_key),
            "denominators": denom,
            "parser_coverage": coverage,
            "thresholds": thresholds,
        })
    return packs
