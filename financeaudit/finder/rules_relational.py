"""Finder rules R15-R19 — relational-integrity channel (Agent C).

Contract (docs/CONTRACTS.md §3, PLAN §3.2 + §3.3): exposes
    RULES = [{"rule_id": "R15", "fn": callable(con, thresholds) -> list[candidate_dict]}, ...]

Design notes
------------
- R15 uses a TYPED evidence-obligation table with explicit observability conditions:
  * goods-receipt obligation applies ONLY to accounts whose vendor-invoice peers
    overwhelmingly (>= R15_GR_PEER_COVERAGE) have goods receipts AND only because the
    dossier ships a goods-receipt table scoped to material/logistics. Services are
    NEVER flagged for a missing warehouse receipt.
  * consulting/service obligations degrade to reference-resolvability (PLAN §3.3):
    a service invoice citing a contract is checked for whether that contract is
    resolvable anywhere in the dossier.
  * approval-log absence is NOT used as evidence of missing approval anywhere
    (journal-level coverage unproven, see source_manifest.population_scope).
- R19 checks number sequences ONLY where the numbering population is proven complete
  (file parse_coverage == 'complete' AND the file is the journal of its own numbering,
  AND the observed numbering is dense). Vendor-assigned numbers (ER..., 2026 purchase
  invoices) are never sequence-checked.
- All descriptions are neutral single sentences; no fraud wording.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# ----------------------------------------------------------------------------- params
R15_GR_PEER_COVERAGE = 0.90   # obligation only where peers overwhelmingly have receipts
R15_GR_PEER_MIN_N = 30
R15_SERVICE_MAX_COVERAGE = 0.10

R17_SIMILAR_MIN_RATIO = 0.78  # similar-name pair (identifiers differ) -> low tier
R17_SIMILAR_STRICT = 0.90     # without same-city corroboration require this ratio
R17_CONFLICT_MAX_RATIO = 0.60 # below this, names for the same account are a conflict
R17_MAX_SIMILAR_PAIRS = 12

R18_TOL = 0.005               # ledger<->ledger comparisons: cent-exact

R19_MAX_GAPS_DENSE = 50       # more gaps than this => numbering rule unproven, skip
R19_MAX_EMIT = 10

_ID_PATTERN = re.compile(r"\b([A-Z]{1,5}-\d{4}(?:-\d{1,4})?)\b")
_CONTRACT_KEYWORDS = re.compile(
    r"\b(Rahmenvertrag|Vertrag|Vereinbarung|Investitionsantrag|Bestellung|Kontrakt)\b")
_REPAIR_VOCAB = re.compile(
    r"(?i)\b(reparatur|instandsetzung|instandhaltung|wartung|austausch|"
    r"general(?:ü|ue)berholung|(?:ü|ue)berholung)\b")
_LEGAL_FORMS = re.compile(
    r"(?i)\b(gmbh(?: & co\.? kg)?|ag|se|kg|ohg|e\.?\s?k\.?|ug|inc|ltd|s\.?a\.?)\b")

_TEXT_SOURCES = [  # (table, text_column, ref_id_column, context_columns)
    ("gl", "text", "entry_id", ("sub_account", "doc_ref")),
    ("vendor_tx", "text", None, ("account", "doc_ref")),
    ("customer_tx", "text", None, ("account", "doc_ref")),
    ("asset_tx", "text", None, ("target", "doc_ref")),
    ("sales_invoices", "note", None, ("customer_account", "invoice_no")),
    ("purchase_invoices_2026", "note", None, ("vendor_account", "invoice_no")),
    ("goods_receipts", "note", None, ("vendor_account", "invoice_ref")),
    ("subsequent_payments", "text", None, ("customer_account", "doc_ref")),
]


# ----------------------------------------------------------------------------- helpers
def _account_names(con) -> dict:
    try:
        return dict(con.execute("SELECT account, name FROM gl_accounts").fetchall())
    except Exception:
        return {}


def _party_label(con, account: str) -> str:
    for table in ("vendors", "customers"):
        try:
            r = con.execute(f"SELECT name FROM {table} WHERE account = ?", [account]).fetchone()
            if r:
                return r[0]
        except Exception:
            pass
    return account or ""


def _entry_source_ids(con, entry_ids) -> list:
    if not entry_ids:
        return []
    ph = ",".join("?" for _ in entry_ids)
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT source_id FROM gl WHERE entry_id IN ({ph}) ORDER BY 1",
        list(entry_ids)).fetchall()]


def _mk(rule_id, rule_name, seq, anomaly_type, tier, entity_key, entity_label,
        entry_ids, source_ids, metrics, denominators, description):
    return {
        "candidate_id": f"{rule_id}-{seq:04d}",
        "rule_id": rule_id,
        "rule_name": rule_name,
        "channel": "relational",
        "anomaly_type": anomaly_type,
        "risk_tier": tier,
        "entity_key": entity_key,
        "entity_label": entity_label,
        "entry_ids": sorted(set(entry_ids)),
        "source_ids": sorted(set(source_ids)),
        "metrics": metrics,
        "denominators": denominators,
        "description": description,
    }


def _resolution_corpus(con) -> list:
    """Everything a narrative document reference could legitimately resolve against:
    parsed pdf/docx units and the dossier file names themselves."""
    corpus = [t or "" for (t,) in con.execute("SELECT text FROM doc_units").fetchall()]
    corpus += [f or "" for (f,) in con.execute("SELECT file FROM source_manifest").fetchall()]
    return corpus


def _resolves(token: str, corpus) -> bool:
    tl = token.lower()
    return any(tl in c.lower() for c in corpus)


def _norm_name(name: str) -> str:
    n = _LEGAL_FORMS.sub(" ", name or "")
    n = re.sub(r"[(),.\-&/]", " ", n)
    return re.sub(r"\s+", " ", n).strip().lower()


def _vendor_invoice_entries(con):
    """One record per Kreditorenrechnung entry: vendor, expense accounts, texts, amounts."""
    rows = con.execute(
        """
        WITH inv AS (SELECT DISTINCT entry_id FROM gl WHERE posting_type = 'Kreditorenrechnung')
        SELECT g.entry_id, MIN(g.doc_ref),
               MAX(CASE WHEN g.hb_account IN ('330000', '332000') THEN g.sub_account END),
               LIST(DISTINCT CASE WHEN g.amount > 0 AND g.hb_account NOT IN ('147000')
                                  THEN g.hb_account END),
               string_agg(DISTINCT g.text, ' | '),
               ROUND(SUM(CASE WHEN g.amount > 0 AND g.hb_account NOT IN ('147000')
                              THEN g.amount ELSE 0 END), 2),
               ROUND(SUM(CASE WHEN g.amount > 0 THEN g.amount ELSE 0 END), 2),
               MIN(g.posting_date)
        FROM gl g JOIN inv USING (entry_id)
        GROUP BY g.entry_id ORDER BY g.entry_id
        """
    ).fetchall()
    out = []
    for entry_id, doc_ref, vendor, accs, texts, net, gross, pd in rows:
        out.append({
            "entry_id": entry_id, "doc_ref": doc_ref, "vendor": vendor,
            "accounts": [a for a in (accs or []) if a],
            "text": texts or "", "net": net or 0.0, "gross": gross or 0.0,
            "posting_date": pd,
        })
    return out


def _gr_invoice_refs(con) -> set:
    return {r[0] for r in con.execute(
        "SELECT DISTINCT invoice_ref FROM goods_receipts WHERE invoice_ref IS NOT NULL").fetchall()}


def _related_party_vendor_accounts(con):
    """Vendor accounts tied to shareholders/affiliates: explicit account references in the
    shareholder list plus vendor names highly similar to shareholder names."""
    out = {}
    sh = con.execute(
        "SELECT source_id, name, COALESCE(relation, ''), COALESCE(note, '') FROM shareholders "
        "WHERE COALESCE(is_section_header, FALSE) = FALSE").fetchall()
    vendors = con.execute("SELECT account, name, source_id FROM vendors").fetchall()
    for sid, sname, relation, note in sh:
        blob = f"{relation} {note}"
        for m in re.finditer(r"(?:Kreditor|Debitor|Personenkonto)\D{0,15}(\d{5,6})", blob):
            out.setdefault(m.group(1), []).append(("account_ref", sname, sid))
        for vacc, vname, vsid in vendors:
            if difflib.SequenceMatcher(None, _norm_name(vname), _norm_name(sname)).ratio() >= 0.85:
                out.setdefault(vacc, []).append(("name_match", sname, sid))
    return out


# ----------------------------------------------------------------------------- R15
def r15_evidence_obligation_gaps(con, thresholds):
    names = _account_names(con)
    corpus = _resolution_corpus(con)
    entries = _vendor_invoice_entries(con)
    gr_refs = _gr_invoice_refs(con)
    asset_accounts = {a for a, in con.execute(
        "SELECT account FROM gl_accounts WHERE account LIKE '0%'").fetchall()}

    # --- observability-driven scope for the goods-receipt obligation -------------
    cov = defaultdict(lambda: [0, 0])  # account -> [n_entries, n_with_gr]
    for e in entries:
        has_gr = e["doc_ref"] in gr_refs
        for a in e["accounts"]:
            if a in asset_accounts:
                continue
            cov[a][0] += 1
            cov[a][1] += int(has_gr)
    gr_scope = {a for a, (n, g) in cov.items()
                if n >= R15_GR_PEER_MIN_N and g / n >= R15_GR_PEER_COVERAGE}
    service_scope = {a for a, (n, g) in cov.items()
                     if a not in gr_scope and (g / n if n else 0) <= R15_SERVICE_MAX_COVERAGE}
    goods_receipts_observable = bool(gr_refs)

    obligation_table = [
        {"obligation_id": "OBL-MAT-GR", "transaction_type": "material_purchase",
         "required_evidence": "goods receipt (Wareneingang)",
         "basis": "peer prevalence within dossier + goods-receipt list scoped to material/logistics",
         "observability": f"accounts with >= {R15_GR_PEER_COVERAGE:.0%} peer coverage and n >= {R15_GR_PEER_MIN_N}: {sorted(gr_scope)}"},
        {"obligation_id": "OBL-SRV-CONTRACT", "transaction_type": "consulting_or_service",
         "required_evidence": "resolvable contract/agreement reference (approval-log absence is NOT used)",
         "basis": "business-type rule, degraded to reference-resolvability (PLAN §3.3)",
         "observability": "only invoices whose posting text cites a contract are testable"},
        {"obligation_id": "OBL-ASSET-IA", "transaction_type": "asset_capitalization",
         "required_evidence": "investment request / technical justification + asset card",
         "basis": "business-type rule (fixed assets)",
         "observability": "reference resolvability + asset master data"},
        {"obligation_id": "OBL-RP-AGREEMENT", "transaction_type": "related_party_service",
         "required_evidence": "agreement + allocation basis observable in dossier",
         "basis": "business-type rule (related parties)",
         "observability": "dossier documents only; bank statements etc. are PBC"},
    ]

    out = []
    population = len(entries)

    # (a) material purchases missing goods receipts ------------------------------
    if goods_receipts_observable and gr_scope:
        peers_with = sum(1 for e in entries
                         if any(a in gr_scope for a in e["accounts"]) and e["doc_ref"] in gr_refs)
        missing = defaultdict(list)
        for e in entries:
            if any(a in gr_scope for a in e["accounts"]) and e["doc_ref"] not in gr_refs:
                missing[e["vendor"] or "unknown"].append(e)
        for vendor, es in sorted(missing.items()):
            entry_ids = [e["entry_id"] for e in es]
            out.append(_mk(
                "R15", "evidence_obligation_gap", len(out) + 1, "rule_based", "medium",
                f"vendor:{vendor}", _party_label(con, vendor),
                entry_ids, _entry_source_ids(con, entry_ids),
                {"obligation_id": "OBL-MAT-GR", "n_invoices": len(es),
                 "net_sum": round(sum(e["net"] for e in es), 2),
                 "doc_refs": [e["doc_ref"] for e in es],
                 "peer_coverage_scope": sorted(gr_scope)},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": peers_with},
                (f"{len(es)} material purchase invoice(s) from vendor {vendor} post to accounts whose "
                 f"peer invoices carry goods receipts in {R15_GR_PEER_COVERAGE:.0%}+ of cases, but no "
                 "matching goods receipt is present in the provided goods-receipt list."),
            ))

    # (b) service invoices citing a contract that does not resolve ---------------
    service_entries = [e for e in entries if any(a in service_scope for a in e["accounts"])]
    citing = [e for e in service_entries if _CONTRACT_KEYWORDS.search(e["text"])]
    resolvable_citations = 0
    groups = defaultdict(list)
    for e in citing:
        ids = _ID_PATTERN.findall(e["text"])
        tokens = ids or [m.group(1) for m in _CONTRACT_KEYWORDS.finditer(e["text"])]
        token = tokens[0]
        if _resolves(token, corpus):
            resolvable_citations += 1
            continue
        groups[(e["vendor"] or "unknown", token)].append(e)
    for (vendor, token), es in sorted(groups.items()):
        entry_ids = [e["entry_id"] for e in es]
        out.append(_mk(
            "R15", "evidence_obligation_gap", len(out) + 1, "rule_based", "medium",
            f"vendor:{vendor}", _party_label(con, vendor),
            entry_ids, _entry_source_ids(con, entry_ids),
            {"obligation_id": "OBL-SRV-CONTRACT", "cited_reference": token,
             "reference_resolvable": False, "n_invoices": len(es),
             "net_sum": round(sum(e["net"] for e in es), 2),
             "gross_sum": round(sum(e["gross"] for e in es), 2),
             "doc_refs": [e["doc_ref"] for e in es],
             "note": "goods receipts are NOT expected for services; approval-log absence not used"},
            {"population_size": len(service_entries), "rule_hits": -1,
             "peers_with_expected_evidence": resolvable_citations},
            (f"{len(es)} service invoice(s) from vendor {vendor} cite '{token}' in the posting text, "
             "but no such document is resolvable anywhere in the provided dossier."),
        ))

    # (c) asset capitalizations -------------------------------------------------
    cap_entries = [e for e in entries if any(a in asset_accounts for a in e["accounts"])]
    cap_with_ref = [e for e in cap_entries if _ID_PATTERN.search(e["text"])
                    or "Investitionsantrag" in e["text"]]
    cap_ref_resolvable = sum(
        1 for e in cap_with_ref
        if all(_resolves(t, corpus) for t in _ID_PATTERN.findall(e["text"]) or ["Investitionsantrag"]))
    repair_named = [e for e in cap_entries if e not in cap_with_ref and _REPAIR_VOCAB.search(e["text"])]
    if repair_named:
        entry_ids = [e["entry_id"] for e in repair_named]
        accs = sorted({a for e in repair_named for a in e["accounts"] if a in asset_accounts})
        asset_cards = con.execute(
            "SELECT DISTINCT sub_account FROM gl WHERE entry_id IN ("
            + ",".join("?" for _ in entry_ids) + ") AND hb_account LIKE '0%' AND sub_account IS NOT NULL",
            entry_ids).fetchall()
        out.append(_mk(
            "R15", "evidence_obligation_gap", len(out) + 1, "rule_based", "medium",
            "account:" + ",".join(accs),
            " / ".join(f"{a} {names.get(a, '')}" for a in accs),
            entry_ids, _entry_source_ids(con, entry_ids),
            {"obligation_id": "OBL-ASSET-IA", "n_capitalizations": len(repair_named),
             "net_sum": round(sum(e["net"] for e in repair_named), 2),
             "gross_sum": round(sum(e["gross"] for e in repair_named), 2),
             "doc_refs": sorted(e["doc_ref"] for e in repair_named),
             "vendors": sorted({e["vendor"] for e in repair_named if e["vendor"]}),
             "repair_terms_found": sorted({m.group(0) for e in repair_named
                                           for m in _REPAIR_VOCAB.finditer(e["text"])}),
             "asset_cards_created": sorted(a for a, in asset_cards),
             "alternative_explanation": "capitalizable component replacement (accounting judgment)"},
            {"population_size": len(cap_entries), "rule_hits": -1,
             "peers_with_expected_evidence": cap_ref_resolvable},
            (f"{len(repair_named)} additions capitalized to fixed-asset accounts carry posting texts "
             "describing repair/maintenance-type work, and no investment request or technical "
             "assessment supporting capitalization is observable in the dossier."),
        ))
    for e in cap_with_ref:
        tokens = _ID_PATTERN.findall(e["text"]) or ["Investitionsantrag"]
        unresolved = [t for t in tokens if not _resolves(t, corpus)]
        if not unresolved:
            continue
        out.append(_mk(
            "R15", "evidence_obligation_gap", len(out) + 1, "rule_based", "medium",
            f"vendor:{e['vendor']}", _party_label(con, e["vendor"]),
            [e["entry_id"]], _entry_source_ids(con, [e["entry_id"]]),
            {"obligation_id": "OBL-ASSET-IA", "cited_reference": unresolved[0],
             "reference_resolvable": False, "net_sum": e["net"], "gross_sum": e["gross"],
             "doc_refs": [e["doc_ref"]]},
            {"population_size": len(cap_entries), "rule_hits": -1,
             "peers_with_expected_evidence": cap_ref_resolvable},
            (f"A capitalized addition of {e['net']:,.2f} EUR references '{unresolved[0]}', which is "
             "not resolvable within the provided dossier."),
        ))

    # (d) related-party services without observable agreement --------------------
    rp = _related_party_vendor_accounts(con)
    rp_agreement_terms = ("umlagevertrag", "umlagevereinbarung", "dienstleistungsvertrag",
                          "umlageschl", "kostenumlagevertrag")
    agreement_observable = any(any(t in c.lower() for t in rp_agreement_terms) for c in corpus)
    if not agreement_observable:
        for vendor, evidence in sorted(rp.items()):
            es = [e for e in entries if e["vendor"] == vendor]
            if not es:
                continue
            entry_ids = [e["entry_id"] for e in es]
            sh_sids = sorted({sid for _, _, sid in evidence})
            out.append(_mk(
                "R15", "evidence_obligation_gap", len(out) + 1, "rule_based", "low",
                f"vendor:{vendor}", _party_label(con, vendor),
                entry_ids, _entry_source_ids(con, entry_ids) + sh_sids,
                {"obligation_id": "OBL-RP-AGREEMENT", "n_invoices": len(es),
                 "gross_sum": round(sum(e["gross"] for e in es), 2),
                 "related_party_basis": [{"kind": k, "shareholder_name": n} for k, n, _ in evidence],
                 "pbc_request": "intercompany service agreement and cost-allocation basis"},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": 0},
                (f"Charges totalling {sum(e['gross'] for e in es):,.2f} EUR from related-party vendor "
                 f"account {vendor} have no service agreement or allocation basis observable in the "
                 "dossier; suggested as a PBC request."),
            ))

    for c in out:
        c["denominators"]["rule_hits"] = len(out)
        c["metrics"]["obligation_table"] = obligation_table
    return out


# ----------------------------------------------------------------------------- R16
def r16_dangling_document_references(con, thresholds):
    corpus = _resolution_corpus(con)
    refs = defaultdict(lambda: {"rows": [], "vendors": Counter(), "entry_ids": set()})

    for table, text_col, entry_col, ctx in _TEXT_SOURCES:
        try:
            cols = [text_col, "source_id"] + ([entry_col] if entry_col else []) + list(ctx)
            rows = con.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        except Exception:
            continue
        for row in rows:
            text = row[0] or ""
            if not text:
                continue
            sid = row[1]
            entry_id = row[2] if entry_col else None
            party = row[3 if entry_col else 2]
            ids = _ID_PATTERN.findall(text)
            tokens = set(ids)
            if not ids:
                tokens |= {m.group(1) for m in _CONTRACT_KEYWORDS.finditer(text)}
            for token in tokens:
                rec = refs[token]
                rec["rows"].append((table, sid))
                if party:
                    rec["vendors"][party] += 1
                if entry_id:
                    rec["entry_ids"].add(entry_id)

    population = len(refs)
    unresolved = {t: rec for t, rec in refs.items() if not _resolves(t, corpus)}
    resolved_n = population - len(unresolved)

    out = []
    for token, rec in sorted(unresolved.items()):
        party = rec["vendors"].most_common(1)[0][0] if rec["vendors"] else None
        entry_ids = sorted(rec["entry_ids"])
        source_ids = sorted({sid for _, sid in rec["rows"]})
        net = None
        if entry_ids:
            net = con.execute(
                "SELECT ROUND(SUM(amount),2) FROM gl WHERE amount > 0 AND hb_account NOT IN ('147000') "
                "AND entry_id IN (" + ",".join("?" for _ in entry_ids) + ")",
                entry_ids).fetchone()[0]
        out.append(_mk(
            "R16", "dangling_document_reference", len(out) + 1, "rule_based", "medium",
            f"reference:{token}" if not party else f"vendor:{party}",
            f"'{token}' cited by {_party_label(con, party) if party else 'multiple records'}",
            entry_ids, source_ids,
            {"reference": token, "n_referring_rows": len(rec["rows"]),
             "referring_tables": sorted({t for t, _ in rec["rows"]}),
             "net_amount_of_referring_entries": net,
             "searched_in": "doc_units (all parsed pdf/docx) + dossier file names",
             "resolved": False},
            {"population_size": population, "rule_hits": len(unresolved),
             "peers_with_expected_evidence": resolved_n},
            (f"Postings reference '{token}' ({len(rec['rows'])} rows), but no document matching this "
             "reference is resolvable within the provided dossier."),
        ))
    return out


# ----------------------------------------------------------------------------- R17
def r17_entity_conflicts(con, thresholds):
    out = []
    vendors = con.execute(
        "SELECT account, name, COALESCE(ustid,''), COALESCE(city,''), source_id FROM vendors "
        "ORDER BY account").fetchall()
    customers = con.execute(
        "SELECT account, name, COALESCE(ustid,''), COALESCE(city,''), source_id FROM customers "
        "ORDER BY account").fetchall()
    population = len(vendors) + len(customers)

    # (a) explicit account assignments in the shareholder list vs master data ----
    sh = con.execute(
        "SELECT source_id, name, COALESCE(relation,''), COALESCE(note,'') FROM shareholders "
        "WHERE COALESCE(is_section_header, FALSE) = FALSE").fetchall()
    masters = {acc: (name, sid) for acc, name, _, _, sid in vendors}
    masters.update({acc: (name, sid) for acc, name, _, _, sid in customers})
    for sid, sname, relation, note in sh:
        blob = f"{relation} {note}"
        for m in re.finditer(r"(?:Kreditor|Debitor|Personenkonto)\D{0,15}(\d{5,6})", blob):
            acc = m.group(1)
            if acc not in masters:
                continue
            mname, msid = masters[acc]
            ratio = difflib.SequenceMatcher(None, _norm_name(sname), _norm_name(mname)).ratio()
            if ratio >= R17_CONFLICT_MAX_RATIO:
                continue
            gl_rows = con.execute(
                "SELECT DISTINCT entry_id FROM gl WHERE sub_account = ?", [acc]).fetchall()
            entry_ids = sorted(e for e, in gl_rows)
            out.append(_mk(
                "R17", "entity_conflict", len(out) + 1, "contextual", "medium",
                f"vendor:{acc}", mname,
                entry_ids, [sid, msid] + _entry_source_ids(con, entry_ids),
                {"conflict_kind": "account_assignment",
                 "shareholder_list_name": sname, "master_data_name": mname,
                 "account": acc, "name_similarity": round(ratio, 2),
                 "assignment_text": blob.strip()},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": 0},
                (f"Account {acc} is assigned to '{sname}' in the shareholder/affiliate list but is "
                 f"named '{mname}' in the master data — two different group entities for the same "
                 "personal account."),
            ))

    # (b) identical USt-ID used by different master records ----------------------
    by_ustid = defaultdict(list)
    for role, recs in (("vendor", vendors), ("customer", customers)):
        for acc, name, ustid, city, sid in recs:
            if ustid:
                by_ustid[ustid].append((role, acc, name, sid))
    for ustid, recs in sorted(by_ustid.items()):
        if len({(r[1]) for r in recs}) < 2:
            continue
        names_ = {r[2] for r in recs}
        out.append(_mk(
            "R17", "entity_conflict", len(out) + 1, "contextual", "medium",
            f"ustid:{ustid}", " / ".join(sorted(names_)),
            [], [r[3] for r in recs],
            {"conflict_kind": "duplicate_ustid", "ustid": ustid,
             "records": [{"role": r[0], "account": r[1], "name": r[2]} for r in recs]},
            {"population_size": population, "rule_hits": -1,
             "peers_with_expected_evidence": 0},
            f"USt-ID {ustid} appears on {len(recs)} different master records.",
        ))

    # (c)+(d) similar/identical names with differing identifiers -> LOW tier -----
    # Two very different situations in this population:
    #   * identical name stem, only the legal form differs (Titan Papier GmbH / AG):
    #     pervasive generator baseline -> ONE grouped low-tier observation
    #   * similar-but-different stem with identical remainder (Nord / Nordlicht
    #     Logistik): genuinely confusable pair -> individual low-tier candidates
    def _split(norm):
        toks = norm.split()
        return (toks[0], " ".join(toks[1:])) if toks else ("", "")

    confusable, same_stem = [], []
    for role, recs in (("vendor", vendors), ("customer", customers)):
        for i in range(len(recs)):
            for j in range(i + 1, len(recs)):
                a, b = recs[i], recs[j]
                na, nb = _norm_name(a[1]), _norm_name(b[1])
                if not na or not nb:
                    continue
                if a[2] and b[2] and a[2] == b[2]:
                    continue  # same USt-ID handled in (b)
                ratio = difflib.SequenceMatcher(None, na, nb).ratio()
                if ratio < R17_SIMILAR_MIN_RATIO:
                    continue
                fa, ra = _split(na)
                fb, rb = _split(nb)
                same_city = a[3] and a[3] == b[3]
                if na == nb:
                    same_stem.append((role, a, b))
                    continue
                stem_sim = (fa != fb and ra == rb and
                            (fa.startswith(fb) or fb.startswith(fa)
                             or difflib.SequenceMatcher(None, fa, fb).ratio() >= 0.7))
                if stem_sim and (same_city or ratio >= R17_SIMILAR_STRICT):
                    confusable.append((ratio, role, a, b, same_city))

    confusable.sort(key=lambda t: -t[0])
    for ratio, role, a, b, same_city in confusable[:R17_MAX_SIMILAR_PAIRS]:
        out.append(_mk(
            "R17", "entity_conflict", len(out) + 1, "contextual", "low",
            f"{role}_pair:{a[0]}|{b[0]}", f"{a[1]} vs {b[1]}",
            [], [a[4], b[4]],
            {"conflict_kind": "similar_names_distinct_identifiers",
             "name_similarity": round(ratio, 3), "same_city": bool(same_city),
             "records": [{"role": role, "account": a[0], "name": a[1], "ustid": a[2], "city": a[3]},
                         {"role": role, "account": b[0], "name": b[1], "ustid": b[2], "city": b[3]}],
             "note": "identifiers (USt-ID) differ; similarity alone is not a conflict"},
            {"population_size": population, "rule_hits": -1,
             "peers_with_expected_evidence": 0},
            (f"Two {role} master records have similar names '{a[1]}' and '{b[1]}' "
             f"(similarity {ratio:.2f}) while carrying distinct USt-IDs."),
        ))
    if same_stem:
        out.append(_mk(
            "R17", "entity_conflict", len(out) + 1, "contextual", "low",
            "population:same_stem_pairs", "identical name stems across legal forms",
            [], sorted({r[4] for _, a, b in same_stem for r in (a, b)})[:40],
            {"conflict_kind": "same_stem_different_legal_form", "n_pairs": len(same_stem),
             "examples": [{"role": role, "a": a[1], "b": b[1]} for role, a, b in same_stem[:10]],
             "note": "pervasive in this master-data population; treated as baseline, listed for completeness"},
            {"population_size": population, "rule_hits": -1,
             "peers_with_expected_evidence": 0},
            (f"{len(same_stem)} master-record pairs share an identical normalized name and differ "
             "only in legal form; the pattern is pervasive across the population and is listed as "
             "a baseline observation."),
        ))

    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# ----------------------------------------------------------------------------- R18
def r18_reconciliation_breaks(con, thresholds):
    out = []
    seq = [0]

    def emit(tier, entity_key, entity_label, entry_ids, source_ids, metrics, description,
             anomaly_type="rule_based"):
        seq[0] += 1
        out.append(_mk("R18", "reconciliation_break", seq[0], anomaly_type, tier,
                       entity_key, entity_label, entry_ids, source_ids, metrics,
                       {"population_size": 0, "rule_hits": -1,
                        "peers_with_expected_evidence": 0}, description))

    # (1) B-class deviations recorded by ingest ---------------------------------
    # Provenance for file-level integrity deviations (row counts / hashes /
    # declared sums that disagree with the Exportprotokoll, or ledger tie-out
    # breaks): cite the Exportprotokoll page — the export-integrity document
    # whose attestations are being violated. Every candidate MUST carry
    # source_ids (CONTRACTS §3); without this, any post-export-modified dossier
    # (e.g. the duplicate-payment mutation) produced empty-source_id candidates
    # and hard-stopped the finder. On a pristine dossier this loop emits nothing
    # (no B-class deviations).
    export_sids = [r[0] for r in con.execute(
        "SELECT source_id FROM source_registry "
        "WHERE file ILIKE '%exportprotokoll%' AND kind = 'page' ORDER BY 1").fetchall()]
    try:
        from financeaudit.core.config import BUILD_DIR
        pt_path = Path(BUILD_DIR) / "property_tests.json"
    except Exception:
        pt_path = Path("build/property_tests.json")
    if pt_path.exists():
        pt = json.loads(pt_path.read_text())
        for t in pt.get("b_class", []):
            if t.get("passed"):
                continue
            if not export_sids:
                # no export-integrity document to cite; the specific ledger
                # tie-out breaks are still emitted with row-level source_ids by
                # blocks (4)/(5)/(6) below — skip the generic summary candidate
                # rather than emit one with empty source_ids.
                continue
            emit("medium", f"property_test:{t['name']}", t["name"], [], export_sids,
                 {"check": "ingest_b_class", "deviation": t.get("deviation"),
                  "detail": t.get("detail")},
                 (f"Data-reconciliation expectation '{t['name']}' shows a deviation of "
                  f"{t.get('deviation')}: {str(t.get('detail'))[:160]}"))

    # (2) sales invoice journal <-> goods issues 1:1 (credit notes exempt) ------
    # Only flag invoices whose SERVICE DATE falls in the audited fiscal year:
    # an invoice with a next-period (e.g. 2026) service date whose goods issue
    # legitimately hasn't happened yet is NOT a current-year revenue-timing
    # issue (finals FP fix 2026-07-18). Carry service_date + note so the claims
    # builder can categorise (bonus reversal / bill-and-hold / storno pair).
    si_cols = {r[0] for r in con.execute("DESCRIBE sales_invoices").fetchall()}
    has_gi_ref = "goods_issue_ref" in si_cols
    has_note = "note" in si_cols
    has_sdate = "service_date" in si_cols
    fy = con.execute("SELECT max(EXTRACT(year FROM posting_date)) FROM gl").fetchone()[0]
    fy = int(fy) if fy else 2025
    note_sel = "s.note" if has_note else "NULL"
    sdate_sel = "s.service_date" if has_sdate else "NULL"
    # no-delivery = no goods_issues row AND (if the journal carries its own
    # goods-issue reference) that reference is empty too.
    gi_clause = "g.invoice_ref IS NULL"
    if has_gi_ref:
        gi_clause = ("g.invoice_ref IS NULL AND "
                     "(s.goods_issue_ref IS NULL OR trim(s.goods_issue_ref) = '')")
    year_clause = ""
    if has_sdate:
        year_clause = (f" AND (s.service_date IS NULL OR "
                       f"EXTRACT(year FROM s.service_date) <= {fy})")
    inv_no_issue = con.execute(
        f"""
        SELECT s.invoice_no, s.customer_account, s.customer_name, s.amount,
               s.source_id, {sdate_sel} AS sdate, {note_sel} AS note
        FROM sales_invoices s LEFT JOIN goods_issues g ON g.invoice_ref = s.invoice_no
        WHERE s.kind <> 'Gutschrift' AND {gi_clause}{year_clause}
        ORDER BY s.amount DESC NULLS LAST, s.invoice_no
        """).fetchall()
    issue_no_inv = con.execute(
        """
        SELECT g.wa_no, g.invoice_ref, g.customer_account, g.amount, g.source_id
        FROM goods_issues g LEFT JOIN sales_invoices s ON g.invoice_ref = s.invoice_no
        WHERE s.invoice_no IS NULL ORDER BY g.wa_no
        """).fetchall()
    # Categorise the in-year no-delivery invoices by remark so the highest-risk
    # patterns (year-end bonus reversal, bill-and-hold, cross-period reversal)
    # surface distinctly; a same-period Storno pair is revenue-neutral and is
    # deliberately NOT emitted (FP discipline).
    def _revcat(note):
        n = (note or "").lower()
        if "umsatzbonus" in n or "rueckbelastung" in n or "rückbelastung" in n:
            return "bonus_reversal", "high"
        if "bill-and-hold" in n or "bill and hold" in n or "einlagerung" in n:
            return "bill_and_hold", "medium"
        if "storno" in n:
            return "storno_pair", "medium"
        return "no_goods_issue", "medium"
    # Credit-note months (kind='Gutschrift') keyed by invoice_no, so a Storno
    # pair whose reversal is in the SAME month can be recognised as revenue-
    # neutral and suppressed (only cross-period pairs are a timing issue).
    cn_month = {}
    if has_sdate:
        for cn_no, cn_sd in con.execute(
                "SELECT invoice_no, service_date FROM sales_invoices "
                "WHERE kind = 'Gutschrift' AND service_date IS NOT NULL").fetchall():
            cn_month[str(cn_no)] = (cn_sd.year, cn_sd.month)
    import re as _re
    for no, acc, cname, amount, sid, sdate, note in inv_no_issue[:60]:
        cat, tier = _revcat(note)
        amt = amount or 0.0
        if cat == "storno_pair":
            # find the paired credit note ref (SCN… in the remark or STORNO_REF)
            m = _re.search(r'S[CG]N\d+', note or "")
            ref = m.group() if m else None
            inv_m = (sdate.year, sdate.month) if sdate else None
            ref_m = cn_month.get(ref) if ref else None
            if inv_m and ref_m and inv_m == ref_m:
                continue  # same-period offset — revenue-neutral, not reported
            if not (inv_m and ref_m and inv_m != ref_m):
                continue  # cannot confirm cross-period — do not over-report
        emit(tier, f"document:{no}", f"{no} ({cname})", [], [sid],
             {"check": "invoice_vs_goods_issue", "invoice_no": no, "amount": amt,
              "customer_account": acc, "service_date": str(sdate) if sdate else None,
              "revenue_category": cat, "remark": (note or "")[:120]},
             f"Sales invoice {no} ({amt:,.2f} EUR) records revenue with no matching "
             f"goods issue (service date {sdate}); peers reconcile 1:1.")
    for wa, ref, acc, amount, sid in issue_no_inv[:20]:
        emit("medium", f"document:{wa}", f"{wa} (ref {ref})", [], [sid],
             {"check": "goods_issue_vs_invoice", "wa_no": wa, "invoice_ref": ref,
              "amount": amount, "customer_account": acc},
             f"Goods issue {wa} references invoice {ref}, which is not present in the sales "
             "invoice journal.")

    # (3) GL revenue documents <-> invoice journal completeness -----------------
    gl_not_journal = con.execute(
        """
        SELECT doc_ref, ROUND(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 2),
               LIST(DISTINCT entry_id)
        FROM gl WHERE posting_type = 'Debitorenrechnung'
          AND (doc_ref LIKE 'AR%' OR doc_ref LIKE 'SG%')
          AND doc_ref NOT IN (SELECT invoice_no FROM sales_invoices)
        GROUP BY doc_ref ORDER BY doc_ref
        """).fetchall()
    for doc, gross, entry_ids in gl_not_journal[:20]:
        entry_ids = sorted(entry_ids)
        reversal = con.execute(
            "SELECT DISTINCT doc_ref FROM gl WHERE text ILIKE '%gutschrift%' AND entry_id IN ("
            "SELECT entry_id FROM gl WHERE sub_account IN (SELECT sub_account FROM gl WHERE doc_ref = ?)"
            ") AND doc_ref <> ?", [doc, doc]).fetchall()
        emit("low", f"document:{doc}", doc, entry_ids, _entry_source_ids(con, entry_ids),
             {"check": "gl_revenue_doc_vs_invoice_journal", "doc_ref": doc,
              "gross_amount": gross,
              "possible_related_docs": sorted(r for r, in reversal)[:5]},
             (f"General-ledger revenue document {doc} (gross {gross:,.2f} EUR) is not present in "
              "the sales invoice journal, which otherwise covers the full numbering range."))

    # (4) OP lists vs subledgers, per account -----------------------------------
    for sub_table, op_table, side in (("customer_tx", "op_debitors_accounts", "debitor"),
                                      ("vendor_tx", "op_creditors_accounts", "creditor")):
        rows = con.execute(
            f"""
            WITH s AS (SELECT account, ROUND(SUM(amount), 2) b, LIST(DISTINCT source_id) sids
                       FROM {sub_table} GROUP BY 1)
            SELECT COALESCE(s.account, o.account), s.b, ROUND(o.balance, 2), o.source_id, s.sids
            FROM s FULL JOIN {op_table} o USING (account)
            WHERE ABS(COALESCE(s.b, 0) - COALESCE(ROUND(o.balance, 2), 0)) > {R18_TOL}
            ORDER BY 1
            """).fetchall()
        for acc, sub_b, op_b, op_sid, sub_sids in rows[:20]:
            sids = ([op_sid] if op_sid else []) + sorted(sub_sids or [])[:10]
            emit("medium", f"{side}:{acc}", _party_label(con, acc), [], sids,
                 {"check": "op_vs_subledger", "account": acc,
                  "subledger_balance": sub_b, "op_list_balance": op_b,
                  "difference": round((sub_b or 0) - (op_b or 0), 2)},
                 (f"The open-item list balance for account {acc} ({op_b if op_b is not None else 'missing'}) "
                  f"differs from the subledger total ({sub_b if sub_b is not None else 'missing'})."))

    # (5) trial balance vs GL, per account --------------------------------------
    rows = con.execute(
        f"""
        WITH g AS (SELECT hb_account account, ROUND(SUM(amount), 2) b FROM gl GROUP BY 1)
        SELECT t.account, t.name, ROUND(t.closing, 2), g.b, t.source_id
        FROM trial_balance t LEFT JOIN g USING (account)
        WHERE ABS(COALESCE(g.b, 0) - ROUND(t.closing, 2)) > {R18_TOL}
        ORDER BY t.account
        """).fetchall()
    for acc, name, tb, glb, sid in rows[:20]:
        emit("medium", f"account:{acc}", f"{acc} {name}", [], [sid],
             {"check": "trial_balance_vs_gl", "account": acc,
              "trial_balance_closing": tb, "gl_total": glb,
              "difference": round((glb or 0) - (tb or 0), 2)},
             f"Trial-balance closing for account {acc} ({tb:,.2f}) differs from the "
             f"general-ledger total ({(glb or 0):,.2f}).")

    # (6) reconciliation sheet: any stated difference that is non-zero ----------
    rows = con.execute(
        "SELECT label, value, source_id FROM reconciliation "
        "WHERE label ILIKE '%differenz%' AND value IS NOT NULL AND ABS(value) > 0").fetchall()
    for label, value, sid in rows:
        emit("medium", f"reconciliation:{label}", label, [], [sid],
             {"check": "reconciliation_sheet", "stated_difference": value},
             f"The reconciliation sheet states a non-zero difference for '{label}': {value:,.2f}.")

    n_checks = 6
    for c in out:
        c["denominators"]["rule_hits"] = len(out)
        c["denominators"]["population_size"] = n_checks
    return out


# ----------------------------------------------------------------------------- R19
def r19_sequence_integrity(con, thresholds):
    out = []
    coverage = dict(con.execute("SELECT file, parse_coverage FROM source_manifest").fetchall())

    #            table            number_col    file substring                 label
    targets = [("sales_invoices", "invoice_no", "Fakturajournal_2025", "sales invoice journal"),
               ("goods_issues", "wa_no", "Warenausgangsliste", "goods issue list"),
               ("goods_receipts", "we_no", "Wareneingangsliste", "goods receipt list")]

    population = 0
    for table, col, file_sub, label in targets:
        file = next((f for f in coverage if file_sub in f), None)
        if file is None or coverage.get(file) != "complete":
            continue  # numbering population not proven complete -> no sequence check
        rows = con.execute(
            f"SELECT {col}, source_id FROM {table} WHERE {col} IS NOT NULL ORDER BY {col}").fetchall()
        nums = {}
        for no, sid in rows:
            m = re.search(r"(\d+)$", no)
            if m:
                nums.setdefault(int(m.group(1)), []).append((no, sid))
        if not nums:
            continue
        population += 1
        lo, hi = min(nums), max(nums)
        gaps = [n for n in range(lo, hi + 1) if n not in nums]
        dupes = {n: v for n, v in nums.items() if len(v) > 1}
        if len(gaps) > R19_MAX_GAPS_DENSE:
            continue  # numbering not dense -> numbering rule unproven, skip silently

        for n, occurrences in sorted(dupes.items())[:R19_MAX_EMIT]:
            out.append(_mk(
                "R19", "sequence_integrity", len(out) + 1, "rule_based", "medium",
                f"sequence:{file_sub}:{n}", f"{label} number {n} duplicated",
                [], [sid for _, sid in occurrences],
                {"check": "duplicate_number", "number": n,
                 "documents": [no for no, _ in occurrences], "file": file},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": len(nums)},
                f"Document number {n} appears {len(occurrences)} times in the {label}.",
            ))
        for n in gaps[:R19_MAX_EMIT]:
            # try resolving the missing number elsewhere (GL doc_ref with any prefix)
            gl_hits = con.execute(
                "SELECT DISTINCT doc_ref FROM gl WHERE doc_ref SIMILAR TO '[A-Z]+' || ? ", [str(n)]
            ).fetchall()
            neighbors = [nums[n - 1][0][1]] if n - 1 in nums else []
            neighbors += [nums[n + 1][0][1]] if n + 1 in nums else []
            entry_ids = []
            if gl_hits:
                ph = ",".join("?" for _ in gl_hits)
                entry_ids = sorted(e for e, in con.execute(
                    f"SELECT DISTINCT entry_id FROM gl WHERE doc_ref IN ({ph})",
                    [d for d, in gl_hits]).fetchall())
            out.append(_mk(
                "R19", "sequence_integrity", len(out) + 1, "rule_based", "low",
                f"sequence:{file_sub}:{n}", f"{label} number {n} missing",
                entry_ids, neighbors + _entry_source_ids(con, entry_ids),
                {"check": "number_gap", "missing_number": n, "range": [lo, hi],
                 "file": file, "resolved_in_gl_as": sorted(d for d, in gl_hits),
                 "population_note": "file parse_coverage=complete and numbering otherwise dense"},
                {"population_size": population, "rule_hits": -1,
                 "peers_with_expected_evidence": len(nums)},
                (f"Number {n} is missing from the otherwise gap-free {label} sequence "
                 f"{lo}-{hi}"
                 + (f"; general-ledger document(s) {', '.join(d for d, in gl_hits)} carry this number."
                    if gl_hits else ".")),
            ))

    for c in out:
        c["denominators"]["rule_hits"] = len(out)
    return out


# ----------------------------------------------------------------------------- export
RULES = [
    {"rule_id": "R15", "rule_name": "evidence_obligation_gap", "channel": "relational",
     "fn": r15_evidence_obligation_gaps},
    {"rule_id": "R16", "rule_name": "dangling_document_reference", "channel": "relational",
     "fn": r16_dangling_document_references},
    {"rule_id": "R17", "rule_name": "entity_conflict", "channel": "relational",
     "fn": r17_entity_conflicts},
    {"rule_id": "R18", "rule_name": "reconciliation_break", "channel": "relational",
     "fn": r18_reconciliation_breaks},
    {"rule_id": "R19", "rule_name": "sequence_integrity", "channel": "relational",
     "fn": r19_sequence_integrity},
]


if __name__ == "__main__":  # self-test against the real build DB
    import duckdb

    from financeaudit.core.config import BUILD_DIR, DB_PATH

    thr_path = BUILD_DIR / "thresholds.json"
    thresholds = json.loads(thr_path.read_text()) if thr_path.exists() else {}
    con = duckdb.connect(str(DB_PATH), read_only=True)
    total = 0
    for rule in RULES:
        cands = rule["fn"](con, thresholds)
        total += len(cands)
        print(f"{rule['rule_id']} {rule['rule_name']}: {len(cands)} candidates")
        for c in cands:
            print(f"   {c['candidate_id']} [{c['risk_tier']}] {c['entity_key']} :: {c['description'][:110]}")
    print(f"TOTAL relational candidates: {total}")
