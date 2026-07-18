"""Static, self-contained printable findings report (build/report.html).

Offline/print fallback that mirrors the redesigned three-pane UI: an executive
summary strip, ranked key findings first (same card section order as the UI —
Summary / Key evidence / Verification / Defense check / Evidence status + PBC /
muted footer), then observations grouped by scheme inside a collapsed section.

English chrome throughout; German is kept only where it is data (file names,
raw quotes, cell values). No external assets, no required JavaScript — native
<details>/<summary> handle collapsing, and print CSS expands everything.
"""
from __future__ import annotations

import html


def _e(x) -> str:
    return html.escape(str(x)) if x is not None else ""


def _amt(v) -> str:
    """Amount for display: '—' when 0/absent, else '248,000.00 EUR'."""
    if not isinstance(v, (int, float)) or v == 0:
        return "—"
    return f"{v:,.2f} EUR"


def _num(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return _e(v)


# English labels for the three-state evidence status (state strings are data).
_EVSTATE = {
    "not_provided_in_materials": "not provided in materials",
    "provided_but_mismatched": "provided but mismatched",
    "parse_failure": "parse failure",
    "parse_incomplete": "parse failure",
}


def _evstate_label(state: str) -> str:
    return _EVSTATE.get(state, str(state).replace("_", " "))


_CSS = """
:root {
  --navy:#16283f; --navy2:#1f4e79; --navy3:#2c5c8f;
  --ink:#1b2530; --mut:#5b6a7a; --faint:#8b98a6;
  --line:#d7dce3; --line2:#b8c0cb; --bg:#eef1f4; --panel:#fff; --panel2:#f8f9fb;
  --ok:#1e6b40; --okbg:#e8f3ec; --okbd:#b9d9c6;
  --bad:#a13323; --badbg:#f9e9e5; --badbd:#e0b4a9;
  --warn:#8a6d1f; --warnbg:#f7f0da; --warnbd:#ddcb92;
  --violet:#4b4390; --violetbg:#ecebf6; --violetbd:#c3bfe0;
  --mono:"SF Mono","Consolas","Liberation Mono",Menlo,monospace;
}
* { box-sizing:border-box; }
body { font:13px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       color:var(--ink); margin:0; background:var(--bg);
       font-feature-settings:"tnum" 1,"lnum" 1; }
.wrap { max-width:1040px; margin:0 auto; padding:22px; }
a { color:var(--navy2); }

/* header */
.rpt-head { border-bottom:2px solid var(--navy); padding-bottom:12px; margin-bottom:14px; }
.rpt-head h1 { font-size:21px; margin:0; color:var(--navy); }
.rpt-sub { color:var(--mut); margin-top:3px; font-size:12.5px; }

/* executive strip */
.exec { display:flex; flex-wrap:wrap; gap:0; background:var(--panel);
        border:1px solid var(--line2); border-radius:6px; padding:12px 6px;
        margin-bottom:8px; }
.exec-item { padding:2px 22px; border-right:1px solid var(--line); }
.exec-item:last-child { border-right:0; }
.exec-val { font-size:20px; font-weight:700; color:var(--navy2); line-height:1.1; }
.exec-item.strong .exec-val { font-size:24px; }
.exec-ok .exec-val { color:var(--ok); }
.exec-warn .exec-val { color:var(--bad); }
.exec-label { font-size:10.5px; text-transform:uppercase; letter-spacing:.5px;
              color:var(--mut); margin-top:3px; }
.exec-foot { color:var(--faint); font-size:11px; margin:2px 0 18px; }

/* section headings */
.rpt-section { font-size:13px; font-weight:700; text-transform:uppercase;
               letter-spacing:.8px; color:var(--navy); margin:26px 0 6px;
               border-bottom:1px solid var(--line2); padding-bottom:5px; }

/* index */
.index { width:100%; border-collapse:collapse; font-size:12px; margin-bottom:8px; }
.index th { text-align:left; color:var(--mut); font-size:10.5px; text-transform:uppercase;
            letter-spacing:.5px; border-bottom:1px solid var(--line2); padding:4px 8px; }
.index td { border-bottom:1px solid var(--line); padding:5px 8px; vertical-align:top; }
.index td.r { text-align:right; font-family:var(--mono); white-space:nowrap; }
.index .rk { font-family:var(--mono); font-weight:700; color:var(--navy2); }

/* card */
.card { background:var(--panel); border:1px solid var(--line2); border-radius:6px;
        margin:12px 0; padding:16px 20px; }
.card.key { page-break-inside:avoid; }
.eyebrow { display:flex; align-items:baseline; gap:10px; margin-bottom:3px; color:var(--mut); }
.rank { font-family:var(--mono); font-weight:700; font-size:13px; color:#fff;
        background:var(--navy2); border-radius:3px; padding:1px 7px; }
.fid { font-family:var(--mono); font-size:11px; }
.entity { font-size:12px; }
.title-row { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
.title-row h2 { font-size:17px; margin:0; color:var(--navy); line-height:1.3; }
.amount { font-size:20px; font-weight:700; white-space:nowrap; text-align:right;
          font-family:var(--mono); }
.amount small { display:block; font-size:10px; font-weight:400; color:var(--mut);
                font-family:inherit; }
.badges { margin-top:9px; display:flex; flex-wrap:wrap; gap:5px; }
.badge, .chip, .tag { display:inline-block; font-size:10px; line-height:1.3;
        padding:3px 7px; border-radius:3px; border:1px solid transparent; }
.b-report { background:var(--navy2); color:#fff; font-weight:600; }
.b-observation { background:#eef2f6; color:var(--navy2); border-color:#bccadb; }
.b-quarantine { background:var(--warnbg); color:#77621c; border-color:var(--warnbd); }
.b-rejected { background:#eee; color:#666; border-color:#ccc; }
.tag { background:#eef1f5; color:var(--mut); border-color:var(--line); }
.tag-scheme { background:#e7eef6; color:var(--navy2); border-color:#c2d2e6; font-weight:600; }

/* card blocks */
.block { padding-top:12px; margin-top:12px; border-top:1px solid var(--line); }
.block h3 { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.8px;
            color:var(--navy2); margin:0 0 8px; }
.subh { font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.5px;
        color:var(--mut); margin:12px 0 5px; }
.desc { color:#2a3742; line-height:1.55; max-width:80ch; }
.note { color:var(--mut); font-size:12px; }
.italic { font-style:italic; }

/* claims (key evidence) */
.claim { border:1px solid var(--line); border-radius:5px; padding:9px 11px;
         margin-bottom:7px; background:var(--panel2); }
.claim-head { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:5px; }
.claim-id { font-family:var(--mono); font-size:10.5px; color:var(--faint); margin-left:auto; }
.figure { font-weight:700; font-size:12.5px; color:var(--navy2); font-family:var(--mono);
          background:#e7eef6; border:1px solid #c2d2e6; border-radius:3px; padding:2px 7px; }
.chip-supported { background:var(--okbg); color:var(--ok); border-color:var(--okbd); font-weight:600; }
.chip-contradicted { background:var(--badbg); color:var(--bad); border-color:var(--badbd); font-weight:600; }
.chip-unverifiable { background:var(--warnbg); color:var(--warn); border-color:var(--warnbd); font-weight:600; }
.assertion { margin:3px 0 7px; line-height:1.5; }
.cites { display:flex; flex-direction:column; gap:3px; }
.cite { font:11px/1.4 var(--mono); color:var(--mut); }
.cite code { background:var(--bg); padding:0 3px; border-radius:2px; color:var(--ink); }
.cite .k { color:var(--navy2); }

/* verification */
.verify { border:1px solid var(--line); border-left:3px solid var(--navy3);
          border-radius:4px; padding:7px 10px; margin-bottom:6px; background:var(--panel2); }
.verify-head { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; margin-bottom:4px; }
.verify-val { font-weight:700; font-family:var(--mono); margin-left:auto; color:var(--navy2); }
.formula { font-family:var(--mono); font-size:11.5px; color:#33404d; background:#f2f5f8;
           border:1px solid var(--line); border-radius:3px; padding:6px 8px;
           white-space:pre-wrap; word-break:break-word; }
.recheck-ok { color:var(--ok); font-size:11.5px; margin-top:5px; font-weight:600; }
.recheck-bad { color:var(--bad); font-size:11.5px; margin-top:5px; font-weight:700; }

/* defense */
.defense-line { font-size:13px; font-weight:600; }
.defense-note { margin-top:4px; font-size:11.5px; color:var(--mut); font-style:italic; }
details { margin-top:8px; }
summary { cursor:pointer; font-size:11.5px; font-weight:600; color:var(--navy2); }
.def-item { padding:6px 0; border-bottom:1px dotted var(--line); }
.def-item:last-child { border-bottom:none; }
.def-q { font-weight:600; font-size:12px; }
.def-r { font-size:11.5px; color:var(--mut); margin-top:2px; }
.res { display:inline-block; font-family:var(--mono); font-size:10px; padding:1px 6px;
       border-radius:3px; margin-right:6px; border:1px solid transparent; }
.res-found { background:var(--okbg); color:var(--ok); border-color:var(--okbd); }
.res-not_found { background:var(--warnbg); color:var(--warn); border-color:var(--warnbd); }
.res-not_checkable { background:#eef1f5; color:var(--mut); border-color:var(--line); }

/* evidence status */
.ev-row { display:flex; gap:8px; align-items:baseline; padding:4px 0; }
.state { font-size:10px; font-weight:700; padding:2px 7px; border-radius:3px; border:1px solid transparent;
         white-space:nowrap; }
.s-not_provided_in_materials { background:var(--warnbg); color:var(--warn); border-color:var(--warnbd); }
.s-provided_but_mismatched { background:var(--violetbg); color:var(--violet); border-color:var(--violetbd); }
.s-parse_failure, .s-parse_incomplete { background:var(--badbg); color:var(--bad); border-color:var(--badbd); }
.legend { margin-top:10px; display:flex; gap:6px; flex-wrap:wrap; font-size:10.5px;
          color:var(--mut); align-items:center; }
ul.plain { margin:4px 0 4px 18px; padding:0; }
ul.plain li { margin:2px 0; line-height:1.4; }

/* footer */
.foot { margin-top:14px; padding-top:10px; border-top:1px solid var(--line); }
.foot-row { display:flex; flex-wrap:wrap; gap:6px 12px; align-items:baseline;
            margin-bottom:6px; font-size:11px; color:var(--faint); }
.foot-key { font-weight:700; text-transform:uppercase; letter-spacing:.5px; font-size:10px; color:var(--mut); }
.denom b { color:var(--mut); font-weight:600; }
.denom code { font-family:var(--mono); color:var(--ink); }
.tag-muted { background:transparent; border-color:var(--line); color:var(--faint); font-size:10px; }

/* observations / grouped */
.obs-group { margin:10px 0; }
.obs-group > summary { font-size:11.5px; font-weight:700; text-transform:uppercase;
                       letter-spacing:.5px; color:var(--mut); }

/* print: expand everything, page-break between key findings */
@media print {
  body { background:#fff; }
  .wrap { max-width:none; padding:0; }
  details { }
  details > summary { display:none; }
  details > *:not(summary) { display:revert !important; }
  .card.key { page-break-before:always; }
  .card.key:first-of-type { page-break-before:auto; }
  .card { border-color:#999; }
  .exec { border-color:#999; }
}
"""


# --------------------------------------------------------------------------
def _citation(ct) -> str:
    loc = ct.get("display_locator") or ct.get("file") or ""
    kind = ct.get("kind")
    head = f"<span class='k'>[{_e(kind)}]</span> <code>{_e(loc)}</code>"
    if kind == "cell":
        tail = f" · {_e(ct.get('col'))} = <code>{_e(ct.get('cell_value'))}</code>"
    else:
        page = ct.get("page_label") or (
            (ct.get("page_index") + 1) if isinstance(ct.get("page_index"), int) else "?")
        quote = (ct.get("quote") or "").strip()
        qtxt = f" · p.{_e(page)} “{_e(quote[:120])}”" if quote else f" · p.{_e(page)}"
        tail = f"{qtxt} <span class='note'>({_e(ct.get('locator_precision'))})</span>"
    return f"<div class='cite'>{head}{tail} · sid {_e(ct.get('source_id'))}</div>"


def _claim_block(c) -> str:
    v = c.get("value") or {}
    fig = ""
    if v.get("value") is not None and v.get("kind") in ("amount", "count"):
        shown = _amt(v.get("value")) if v.get("kind") == "amount" \
            else f"{_num(v.get('value'))} {_e(v.get('currency_or_unit') or '')}".strip()
        fig = f"<span class='figure'>{_e(shown)}</span>"
    verdict = c.get("verdict", "unverifiable")
    cites = "".join(_citation(ct) for ct in c.get("citations", []))
    return (
        f"<div class='claim'>"
        f"<div class='claim-head'>"
        f"<span class='chip chip-{_e(verdict)}'>{_e(verdict)}</span>"
        f"<span class='tag'>{_e(c.get('type', '?'))}</span>"
        f"{fig}"
        f"<span class='claim-id'>{_e(c.get('claim_id', '?'))}</span>"
        f"</div>"
        f"<div class='assertion'>{_e(c.get('assertion', '(assertion missing — quarantined)'))}</div>"
        f"<div class='cites'>{cites}</div>"
        f"</div>")


def _verify_block(f) -> str:
    rows = []
    for c in f.get("claims", []):
        v = c.get("value") or {}
        rec = c.get("recompute") or {}
        if not v.get("formula") and not rec.get("applicable"):
            continue
        shown = _amt(v.get("value")) if v.get("kind") == "amount" \
            else f"{_num(v.get('value'))} {_e(v.get('currency_or_unit') or '')}".strip()
        status = ""
        if rec.get("applicable"):
            ok = rec.get("match") is True
            cls = "recheck-ok" if ok else "recheck-bad"
            word = "matches" if ok else "DIFFERS"
            status = (f"<div class='{cls}'>recomputed "
                      f"{_e(rec.get('recomputed_value'))} — {word}"
                      f" <span class='note'>(engine {_e(rec.get('engine'))}, op {_e(rec.get('op'))})</span></div>")
        rows.append(
            f"<div class='verify'>"
            f"<div class='verify-head'>"
            f"<span class='claim-id'>{_e(c.get('claim_id'))}</span>"
            f"<span class='tag'>{_e(c.get('type'))}</span>"
            f"<span class='verify-val'>{_e(shown)}</span></div>"
            f"<div class='formula'>{_e(v.get('formula') or '(no formula string)')}</div>"
            f"{status}</div>")
    if not rows:
        return "<div class='note italic'>No recomputable formula in this finding.</div>"
    return "".join(rows)


def _defense_block(f) -> str:
    checks = f.get("innocence_checked") or []
    counter = f.get("counterevidence") or []
    n_counter = len(counter)
    counter_txt = ("no counterevidence found" if n_counter == 0
                   else f"{n_counter} counterevidence item(s)")
    line = f"{len(checks)} innocence checks · {counter_txt}"

    log = ""
    for p in checks:
        result = p.get("result", "")
        log += (f"<div class='def-item'>"
                f"<div class='def-q'>{_e(p.get('question') or p.get('predicate') or '')}</div>"
                f"<div class='def-r'><span class='res res-{_e(result)}'>{_e(result)}</span>"
                f"<span>{_e(p.get('reason') or p.get('detail') or '')}</span></div>"
                f"</div>")
    for c in counter:
        txt = c.get("description") if isinstance(c, dict) else str(c)
        sids = ", ".join(filter(None, (c.get("source_ids") or []))) if isinstance(c, dict) else ""
        log += (f"<div class='def-item'><div class='def-r'>{_e(txt)}"
                + (f" <span class='cite'>sids: {_e(sids)}</span>" if sids else "")
                + "</div></div>")
    body = log or "<div class='note italic'>No innocence checks recorded.</div>"
    expand = (f"<details><summary>Show defense log</summary><div>{body}</div></details>"
              if (checks or counter) else "")
    return (f"<div class='defense-line'>{_e(line)}</div>"
            f"<div class='defense-note'>Absence of exculpation is not inculpatory evidence.</div>"
            f"{expand}")


def _evidence_status_block(f) -> str:
    ev = f.get("evidence_status") or []
    rows = ""
    for x in ev:
        st = x.get("state", "not_provided_in_materials")
        rows += (f"<div class='ev-row'>"
                 f"<span class='state s-{_e(st)}'>{_e(_evstate_label(st))}</span>"
                 f"<span>{_e(x.get('item'))}"
                 + (f" <span class='note'>— {_e(x.get('note'))}</span>" if x.get("note") else "")
                 + "</span></div>")
    if not rows:
        rows = "<div class='note italic'>No open evidence items.</div>"
    legend = ("<div class='legend'><b>Legend:</b>"
              "<span class='state s-not_provided_in_materials'>not provided in materials</span>"
              "<span class='state s-parse_failure'>parse failure</span>"
              "<span class='state s-provided_but_mismatched'>provided but mismatched</span></div>")
    pbc = f.get("pbc_requests") or []
    pbc_html = ""
    if pbc:
        pbc_html = ("<div class='subh'>Client requests (PBC)</div><ul class='plain'>"
                    + "".join(f"<li>{_e(x)}</li>" for x in pbc) + "</ul>")
    return rows + legend + pbc_html


def _footer_block(f) -> str:
    den = f.get("denominators") or {}
    den_html = " ".join(
        f"<span class='denom'><b>{_e(k.replace('_', ' '))}</b>: <code>{_e(_num(v))}</code></span>"
        for k, v in den.items()) or "<span class='note'>—</span>"
    tags = "".join(f"<span class='tag tag-muted'>{_e(lbl)}</span>" for lbl in (
        f"Mechanism: {f.get('mechanism') or '—'}",
        f"Anomaly: {f.get('anomaly_type') or '—'}",
        f"Parser: {f.get('parser_coverage') or '—'}",
        f"Finding: {f.get('finding_id')}",
        f"llm_used: {str(bool(f.get('llm_used'))).lower()}",
    ))
    return (f"<div class='foot'>"
            f"<div class='foot-row'><span class='foot-key'>Denominators</span>{den_html}</div>"
            f"<div class='foot-row'>{tags}</div></div>")


def _card(f, rank=None) -> str:
    disp = f.get("disposition", "observation")
    core = [c for c in f.get("claims", []) if c.get("role") == "core"]
    supp = [c for c in f.get("claims", []) if c.get("role") != "core"]

    rank_html = f"<span class='rank'>{rank:02d}</span>" if rank else ""
    entity = f.get("entity_label") or f.get("entity_key") or ""
    badges = (f"<span class='badge b-{_e(disp)}'>{_e(disp)}</span>"
              f"<span class='tag tag-scheme'>{_e(f.get('scheme'))}</span>"
              f"<span class='tag'>Anomaly: {_e(f.get('anomaly_type'))}</span>"
              f"<span class='tag'>Status: {_e(f.get('reporting_status'))}</span>")

    core_html = "".join(_claim_block(c) for c in core) or "<div class='note'>—</div>"
    supp_html = ""
    if supp:
        supp_html = (f"<details><summary>Show {len(supp)} supporting claims</summary>"
                     f"<div>{''.join(_claim_block(c) for c in supp)}</div></details>")

    return (
        f"<div class='card {'key' if rank else 'obs'}' id='{_e(f['finding_id'])}'>"
        f"<div class='eyebrow'>{rank_html}"
        f"<span class='fid'>{_e(f['finding_id'])}</span>"
        f"<span class='entity'>{_e(entity)}</span></div>"
        f"<div class='title-row'>"
        f"<h2>{_e(f.get('title'))}</h2>"
        f"<div class='amount'>{_e(_amt(f.get('amount_eur')))}"
        f"<small>amount per recomputation</small></div></div>"
        f"<div class='badges'>{badges}</div>"

        f"<div class='block'><h3>Summary</h3>"
        f"<p class='desc'>{_e(f.get('description_llm') or f.get('description'))}"
        f"{'<span class=llmtag>AI-drafted narrative (facts locked, post-filtered)</span>' if f.get('description_llm') else ''}</p>"
        f"{_next_steps_html(f)}</div>"

        f"<div class='block'><h3>Key evidence</h3>{core_html}{supp_html}</div>"

        f"<div class='block'><h3>Verification</h3>{_verify_block(f)}</div>"

        f"<div class='block'><h3>Defense check</h3>{_defense_block(f)}</div>"

        f"<div class='block'><h3>Evidence status / PBC</h3>{_evidence_status_block(f)}</div>"

        f"{_footer_block(f)}"
        f"</div>")


# --------------------------------------------------------------------------
_SCHEME_ORDER_HINT = ["controls_breach", "related_party"]


def _exec_strip(findings, meta) -> str:
    report = [f for f in findings if f.get("disposition") == "report"]
    n_report = len(report)
    n_obs = sum(1 for f in findings if f.get("disposition") == "observation")
    flagged = sum(f.get("amount_eur") or 0 for f in report)
    n_quar = meta.get("n_quarantined", 0)
    n_cit = sum(len(c.get("citations", [])) for f in findings for c in f.get("claims", []))
    n_cit_located = sum(
        1 for f in findings for c in f.get("claims", [])
        for ct in c.get("citations", []) if ct.get("source_id"))
    llm = str(bool(meta.get("llm_used"))).lower()

    items = [
        ("strong", f"{n_report}", "key findings"),
        ("strong", _amt(flagged), "flagged amount"),
        ("", f"{n_obs}", "observations"),
        ("exec-ok" if n_cit_located == n_cit else "", f"{n_cit}", "citations located"),
        ("exec-ok" if n_quar == 0 else "exec-warn", f"{n_quar}", "quarantine"),
        ("", llm, "llm_used"),
    ]
    cells = "".join(
        f"<div class='exec-item {cls}'><div class='exec-val'>{_e(val)}</div>"
        f"<div class='exec-label'>{_e(lbl)}</div></div>"
        for cls, val, lbl in items)
    return (f"<div class='exec'>{cells}</div>"
            f"<div class='exec-foot'>Generated {_e(meta.get('generated_at'))} · "
            f"deterministic pipeline · every EUR figure recomputed with "
            f"decimal.Decimal over cited raw amounts (zero tolerance). "
            f"Citation resolvability is verified separately by evalx "
            f"(build/eval_report.json).</div>")




def _next_steps_html(f: dict) -> str:
    steps = f.get("next_steps_llm") or []
    if not steps:
        return ""
    lis = "".join(f"<li>{_e(x)}</li>" for x in steps[:3])
    return (f"<div class='nextsteps'><span class='ns-h'>Suggested next steps "
            f"(AI-drafted)</span><ul>{lis}</ul></div>")

def render(findings: list[dict], meta: dict) -> str:
    report = sorted((f for f in findings if f.get("disposition") == "report"),
                    key=lambda f: (f.get("rank") if isinstance(f.get("rank"), int) else 99,
                                   f.get("finding_id", "")))
    observations = [f for f in findings if f.get("disposition") == "observation"]
    quarantine = [f for f in findings if f.get("disposition") == "quarantine"]
    rejected = [f for f in findings if f.get("disposition") == "rejected"]

    # key-findings quick index
    index_rows = "".join(
        f"<tr><td class='rk'>{(i + 1):02d}</td>"
        f"<td><a href='#{_e(f['finding_id'])}'>{_e(f['finding_id'])}</a></td>"
        f"<td>{_e(f.get('scheme'))}</td>"
        f"<td>{_e(f.get('title'))}</td>"
        f"<td class='r'>{_e(_amt(f.get('amount_eur')))}</td></tr>"
        for i, f in enumerate(report))
    index = (f"<table class='index'><tr><th>#</th><th>id</th><th>scheme</th>"
             f"<th>title</th><th style='text-align:right'>amount</th></tr>"
             f"{index_rows}</table>")

    key_cards = "".join(_card(f, rank=(i + 1)) for i, f in enumerate(report))

    # observations grouped by scheme, collapsed
    by_scheme: dict[str, list] = {}
    for f in observations:
        by_scheme.setdefault(f.get("scheme", "?"), []).append(f)
    ordered = sorted(by_scheme.items(),
                     key=lambda kv: (-len(kv[1]), kv[0]))
    breakdown = " · ".join(f"{k} ({len(v)})" for k, v in ordered)
    groups_html = ""
    for scheme, items in ordered:
        cards = "".join(_card(f) for f in sorted(items, key=lambda x: x.get("finding_id", "")))
        groups_html += (f"<details class='obs-group'><summary>{_e(scheme)} "
                        f"({len(items)})</summary><div>{cards}</div></details>")
    obs_section = ""
    if observations:
        obs_section = (
            f"<div class='rpt-section'>Observations ({len(observations)})</div>"
            f"<details><summary>Show {len(observations)} observations — {_e(breakdown)}</summary>"
            f"<div>{groups_html}</div></details>")

    quar_section = ""
    if quarantine:
        quar_section = (f"<div class='rpt-section'>Quarantine ({len(quarantine)})</div>"
                        + "".join(_card(f) for f in quarantine))
    rej_section = ""
    if rejected:
        rej_cards = "".join(_card(f) for f in rejected)
        rej_section = (f"<div class='rpt-section'>Rejected ({len(rejected)})</div>"
                       f"<details><summary>Show {len(rejected)} rejected</summary>"
                       f"<div>{rej_cards}</div></details>")

    sup = meta.get("fixture_supplemented") or []
    sup_note = (f"<div class='exec-foot'>Fixture-supplemented candidates in this run: "
                f"{_e(', '.join(sup))}.</div>" if sup else "")

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FinanceAudit — Findings Report (Muster Verpackungen GmbH, FY2025)</title>
<style>{_CSS}.llmtag{{display:inline-block;margin-left:8px;font-size:10px;color:#64748b;border:1px solid #cbd5e1;border-radius:3px;padding:0 5px;vertical-align:middle}}.nextsteps{{margin-top:8px;font-size:12.5px}}.nextsteps .ns-h{{font-weight:600;color:#334155}}.nextsteps ul{{margin:4px 0 0 18px}}</style>
</head><body>
<div class="wrap">
<div class="rpt-head">
  <h1>FinanceAudit — Findings Report</h1>
  <div class="rpt-sub">Muster Verpackungen GmbH · FY2025 · GDPdU data-carrier disclosure</div>
</div>
{_exec_strip(findings, meta)}
{sup_note}
<div class="rpt-section">Key findings ({len(report)})</div>
{index}
{key_cards}
{obs_section}
{quar_section}
{rej_section}
</div>
</body></html>
"""
