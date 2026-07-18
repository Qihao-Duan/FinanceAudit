"""Static self-contained printable evidence-card report (build/report.html).

One card per finding: assertion tree with per-claim verdicts, formula +
recomputed value, defense log ('no counterevidence found' phrasing),
denominators, three-state evidence status, every citation with
file + display_locator. No external assets, prints cleanly.
"""
from __future__ import annotations

import html


def _e(x) -> str:
    return html.escape(str(x)) if x is not None else ""


_CSS = """
:root { --ink:#1a1d21; --mut:#5c6570; --line:#d8dde3; --bg:#f6f7f9;
        --rep:#8a1f1f; --obs:#8a6d1f; --rej:#555; --ok:#1f6f3c; --bad:#a12626; }
* { box-sizing: border-box; }
body { font: 13px/1.5 "Helvetica Neue", Arial, sans-serif; color: var(--ink);
       margin: 0; background: var(--bg); }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px; }
h1 { font-size: 21px; margin: 0 0 2px; }
h2 { font-size: 15px; margin: 0; }
.sub { color: var(--mut); margin-bottom: 18px; }
.meta-tbl td { padding: 1px 14px 1px 0; color: var(--mut); }
.card { background: #fff; border: 1px solid var(--line); border-radius: 6px;
        margin: 18px 0; padding: 18px 20px; page-break-inside: avoid; }
.badge { display: inline-block; font-size: 10.5px; font-weight: 700;
         letter-spacing: .04em; text-transform: uppercase; padding: 2px 8px;
         border-radius: 3px; margin-right: 6px; border: 1px solid; }
.b-report { color: var(--rep); border-color: var(--rep); }
.b-observation { color: var(--obs); border-color: var(--obs); }
.b-rejected, .b-quarantine { color: var(--rej); border-color: var(--rej); }
.b-tier { color: var(--mut); border-color: var(--line); }
.amt { float: right; font-size: 16px; font-weight: 700; }
.desc { margin: 10px 0; }
.note { color: var(--mut); font-size: 12px; }
table.cl { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }
table.cl th { text-align: left; border-bottom: 1.5px solid var(--ink);
              padding: 4px 6px; font-size: 11px; text-transform: uppercase;
              letter-spacing: .03em; }
table.cl td { border-bottom: 1px solid var(--line); padding: 5px 6px;
              vertical-align: top; }
.v-supported { color: var(--ok); font-weight: 700; }
.v-contradicted { color: var(--bad); font-weight: 700; }
.v-unverifiable { color: var(--obs); font-weight: 700; }
.formula { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 11px;
           background: var(--bg); padding: 4px 6px; border-radius: 3px;
           display: block; margin-top: 3px; overflow-x: auto; white-space: pre-wrap; }
.cit { color: var(--mut); font-size: 11px; }
.cit code { background: var(--bg); padding: 0 3px; border-radius: 2px; }
.sec { margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 8px; }
.sec h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
          margin: 0 0 6px; color: var(--mut); }
.kv { display: inline-block; margin: 0 14px 4px 0; }
.kv b { font-weight: 600; }
ul { margin: 4px 0 4px 18px; padding: 0; }
.state { font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 3px; }
.s-not_provided_in_materials { background: #f3e6c8; color: #7a5c10; }
.s-provided_but_mismatched { background: #f3d3d3; color: #7a2020; }
.s-parse_incomplete { background: #e0e0e0; color: #555; }
@media print { body { background: #fff; } .card { border-color: #999; margin: 10px 0; } }
"""


def _claim_row(c) -> str:
    v = c.get("value") or {}
    rec = c.get("recompute") or {}
    val_html = ""
    if v.get("kind") == "amount":
        match = rec.get("match")
        mtxt = ("recomputed " + _e(rec.get("recomputed_value"))
                + (" = stated (exact)" if match else " != stated — MISMATCH")
                ) if rec.get("applicable") else "not recomputed"
        num = v.get("value")
        num_txt = f"{num:,.2f}" if isinstance(num, (int, float)) else _e(num)
        val_html = (f"<b>{num_txt} {_e(v.get('currency_or_unit'))}</b>"
                    f"<span class='formula'>{_e(v.get('formula'))}\n"
                    f"[{_e(mtxt)}] · op={_e(v.get('op'))} · "
                    f"hash={_e(v.get('input_set_hash'))}</span>")
    elif v.get("kind") not in (None, "none"):
        val_html = f"<b>{_e(v.get('value'))} {_e(v.get('currency_or_unit') or '')}</b>"
    cits = "<br>".join(
        f"<span class='cit'>[{_e(ct.get('kind'))}] <code>{_e(ct.get('display_locator') or ct.get('file'))}</code>"
        + (f" · {_e(ct.get('col'))} = <code>{_e(ct.get('cell_value'))}</code>"
           if ct.get("kind") == "cell" else
           f" · “{_e((ct.get('quote') or '')[:110])}” ({_e(ct.get('locator_precision'))})")
        + f" · sid {_e(ct.get('source_id'))}</span>"
        for ct in c.get("citations", []))
    verdict = c.get("verdict", "unverifiable")
    return (f"<tr><td>{_e(c.get('claim_id', '?'))}<br><span class='note'>"
            f"{_e(c.get('role', '?'))} · {_e(c.get('type', '?'))}</span></td>"
            f"<td>{_e(c.get('assertion', '(assertion missing — quarantined)'))}"
            f"<br>{cits}</td>"
            f"<td>{val_html}</td>"
            f"<td class='v-{_e(verdict)}'>{_e(verdict)}</td></tr>")


def _defense_section(f) -> str:
    dl = f.get("defense_log") or {}
    rows = "".join(
        f"<tr><td>{_e(p['predicate'])}</td><td>{_e(p['question'])}</td>"
        f"<td class='v-{'supported' if p['result'] == 'found' else ('unverifiable' if p['result'] == 'not_checkable' else 'contradicted')}'>"
        f"{_e(p['result'])}</td>"
        f"<td class='note'>{_e(p.get('reason') or p.get('detail') or '')}</td></tr>"
        for p in f.get("innocence_checked", []))
    counter = "".join(f"<li>{_e(c['description'])} <span class='cit'>sids: "
                      f"{_e(', '.join(filter(None, c.get('source_ids', []))) or '—')}</span></li>"
                      for c in f.get("counterevidence", []))
    return (f"<div class='sec'><h3>Defense — innocence predicates checked</h3>"
            f"<table class='cl'><tr><th>predicate</th><th>question</th>"
            f"<th>result</th><th>detail</th></tr>{rows}</table>"
            f"<p class='note'><b>{_e(dl.get('statement', ''))}</b> "
            f"(found: {dl.get('n_found', 0)}, not found: {dl.get('n_not_found', 0)}, "
            f"not checkable: {dl.get('n_not_checkable', 0)}; a 'not found' innocence "
            f"predicate is not evidence of guilt — the retrieval status is disclosed "
            f"in full)</p>"
            + (f"<ul>{counter}</ul>" if counter else "")
            + "</div>")


def _card(f) -> str:
    disp = f.get("disposition", "observation")
    amount = f.get("amount_eur") or 0
    badges = (f"<span class='badge b-{_e(disp)}'>{_e(disp)}</span>"
              f"<span class='badge b-tier'>tier {_e(f.get('risk_tier'))}</span>"
              f"<span class='badge b-tier'>scheme: {_e(f.get('scheme'))}</span>"
              f"<span class='badge b-tier'>mechanism: {_e(f.get('mechanism'))}</span>"
              f"<span class='badge b-tier'>{_e(f.get('anomaly_type'))}</span>")
    amt = f"<span class='amt'>{amount:,.2f} EUR</span>" if amount else ""
    ind = f.get("intent_indicators") or []
    wording = f.get("wording_level")
    wtxt = {"scheme_pattern": "Intent indicators present — scheme classification "
                              "applies (see list); wording remains subject to "
                              "confirmation, no accusation is made.",
            "control_deficiency": "No intent indicators — reported as control "
                                  "deficiency / needs review.",
            "needs_review": "Observation level — needs review.",
            "n/a": ""}.get(wording, "")
    ind_html = ("<ul>" + "".join(f"<li>{_e(i)}</li>" for i in ind) + "</ul>") if ind else ""
    core = [c for c in f.get("claims", []) if c.get("role") == "core"]
    supp = [c for c in f.get("claims", []) if c.get("role") != "core"]
    tree = ""
    for label, group in (("Core claims", core), ("Supporting claims", supp)):
        if group:
            tree += (f"<div class='sec'><h3>{label}</h3><table class='cl'>"
                     f"<tr><th style='width:9%'>id</th><th style='width:51%'>assertion "
                     f"&amp; citations</th><th style='width:30%'>value / formula / "
                     f"recompute</th><th>verdict</th></tr>"
                     + "".join(_claim_row(c) for c in group) + "</table></div>")
    ev = f.get("evidence_status") or []
    ev_html = ""
    if ev:
        ev_html = ("<div class='sec'><h3>Evidence status (three-state)</h3><table class='cl'>"
                   + "".join(f"<tr><td>{_e(x['item'])}</td>"
                             f"<td><span class='state s-{_e(x['state'])}'>{_e(x['state'])}"
                             f"</span> <span class='note'>{_e(x.get('note') or '')}</span>"
                             f"</td></tr>" for x in ev)
                   + "</table></div>")
    den = f.get("denominators") or {}
    den_html = " ".join(f"<span class='kv'><b>{_e(k)}</b>: {_e(v)}</span>"
                        for k, v in den.items())
    gate = f.get("gate") or {}
    pbc = f.get("pbc_requests") or []
    pbc_html = ("<div class='sec'><h3>PBC — material to request</h3><ul>"
                + "".join(f"<li>{_e(x)}</li>" for x in pbc) + "</ul></div>") if pbc else ""
    fixture_note = ("<p class='note'>NOTE: built on fixture-supplemented candidates "
                    "(sibling finder rules not yet delivered for this family).</p>"
                    if f.get("fixture_supplemented") else "")
    return f"""
<div class='card' id='{_e(f['finding_id'])}'>
  {amt}<h2>{_e(f['finding_id'])} · {_e(f.get('title'))}</h2>
  <div style='margin:6px 0'>{badges}</div>
  <p class='desc'>{_e(f.get('description'))}</p>
  <p class='note'>{_e(wtxt)}</p>{ind_html}{fixture_note}
  <p class='note'>gate: {_e(f.get('gate_reason'))} · core supported
  {gate.get('core_supported', '?')}/{gate.get('n_core', '?')} · recompute failures:
  {_e(', '.join(gate.get('amount_recompute_failures', [])) or 'none')}</p>
  {tree}
  {_defense_section(f)}
  {ev_html}
  <div class='sec'><h3>Denominators</h3>{den_html or "<span class='note'>—</span>"}</div>
  {pbc_html}
</div>"""


def render(findings: list[dict], meta: dict) -> str:
    counts = {}
    for f in findings:
        counts[f.get("disposition", "?")] = counts.get(f.get("disposition", "?"), 0) + 1
    toc = "".join(
        f"<tr><td><a href='#{_e(f['finding_id'])}'>{_e(f['finding_id'])}</a></td>"
        f"<td><span class='badge b-{_e(f.get('disposition'))}'>{_e(f.get('disposition'))}"
        f"</span></td><td>{_e(f.get('title'))}</td>"
        f"<td style='text-align:right'>{(f.get('amount_eur') or 0):,.2f}</td></tr>"
        for f in findings)
    cards = "".join(_card(f) for f in findings)
    sup = meta.get("fixture_supplemented") or []
    sup_note = (f"<p class='note'>Fixture-supplemented candidates in this run: "
                f"{_e(', '.join(sup))} — replace with finder output when Agent C "
                f"delivers R15/R16/R17.</p>" if sup else "")
    return f"""<!doctype html>
<meta charset="utf-8">
<title>FinanceAudit — Findings Report (Muster Verpackungen GmbH, FY2025)</title>
<style>{_CSS}</style>
<div class="wrap">
<h1>FinanceAudit — Findings Report</h1>
<div class="sub">Muster Verpackungen GmbH · FY2025 GDPdU dossier · generated
{_e(meta.get('generated_at'))} · llm_used: false (deterministic pipeline)</div>
<table class="meta-tbl">
<tr><td>candidates source</td><td>{_e(meta.get('candidates_source'))}</td></tr>
<tr><td>dispositions</td><td>{_e(', '.join(f'{k}: {v}' for k, v in sorted(counts.items())))}</td></tr>
<tr><td>amount claims recomputed exactly</td>
<td>{meta.get('n_amount_recompute_match')}/{meta.get('n_amount_claims')}</td></tr>
<tr><td>quarantined</td><td>{meta.get('n_quarantined', 0)}</td></tr>
</table>
{sup_note}
<p class="note">Every EUR figure is recomputed with decimal.Decimal from the cited
rows' exact raw amount strings (zero tolerance). 'Not found' phrasing follows the
population-scope language gate: absence is only asserted where a file's declared
scope covers the evidence type and parsing is complete.</p>
<div class="card"><h2>Contents</h2><table class="cl">
<tr><th>id</th><th>disposition</th><th>title</th><th style='text-align:right'>EUR</th></tr>
{toc}</table></div>
{cards}
</div>
"""
