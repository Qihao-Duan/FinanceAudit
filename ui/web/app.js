// FinanceAudit — auditor-facing evidence viewer.
// Left: key findings + collapsed observations + PBC. Center: evidence card
// (fixed section order). Right: source viewer with cited-location highlight.
import {
  api, esc, eur, num, pct, recompute,
  t, tf, label, getLocale, setLocale, normEvState,
} from "./fmt.js";

const $ = (id) => document.getElementById(id);

const state = {
  status: null,
  manifest: null,
  list: [],
  detail: {},        // finding_id -> full finding (cache)
  currentId: null,
  sourceLoaded: false,
};

// ================================================================= startup ==
async function init() {
  bindLangToggle();
  try {
    const [status, manifest, list] = await Promise.all([
      api("/api/status"), api("/api/manifest"), api("/api/findings"),
    ]);
    state.status = status;
    state.manifest = manifest;
    state.list = list;
    renderChrome();
    renderSourceHint();
    const first = pickFirst(list);
    if (first) selectFinding(first);
    else $("evidence-card").textContent = t("card_pick");
  } catch (e) {
    $("findings-list").innerHTML =
      `<div class="empty-state">${esc(tf("findings_error", { msg: e.message }))}</div>`;
  }
}

function pickFirst(list) {
  const report = list.filter((f) => f.disposition === "report")
    .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  return (report[0] || list[0] || {}).finding_id || null;
}

function bindLangToggle() {
  document.querySelectorAll("#lang-toggle button").forEach((btn) =>
    btn.addEventListener("click", () => setLang(btn.dataset.loc)));
}

function setLang(loc) {
  setLocale(loc);
  renderChrome();
  if (state.currentId && state.detail[state.currentId]) {
    renderCard(state.detail[state.currentId]);
  } else {
    $("evidence-card").textContent = t("card_pick");
  }
  if (!state.sourceLoaded) renderSourceHint();
}

// ============================================================= chrome shell ==
function renderChrome() {
  document.documentElement.lang = getLocale();
  renderTopbar();
  renderExec();
  renderList();
  renderPbc();
  $("llm-note").textContent = t("footer_llm");
}

function renderTopbar() {
  const { status, manifest } = state;
  $("brand-sub").textContent = t("brand_sub");

  const modeEl = $("stat-mode");
  modeEl.textContent = status.mode === "fixtures" ? t("mode_fixtures") : t("mode_build");
  modeEl.className = "stat " + (status.mode === "fixtures" ? "warn" : "good");

  const s = manifest.summary || {};
  const cov = $("stat-coverage");
  cov.textContent = tf("coverage", {
    pct: s.parse_coverage_pct != null ? s.parse_coverage_pct + " %" : "–",
    done: s.n_files_complete ?? "–",
    total: s.n_files ?? "–",
  });
  cov.className = "stat" + ((s.parse_coverage_pct ?? 0) >= 100 ? " good" : "");

  const bal = manifest.gl_balance;
  const b = $("stat-balance");
  if (bal && typeof bal.difference === "number") {
    b.style.display = "";
    b.textContent = tf("gl_balance", { diff: eur(bal.difference, { dashZero: false }) });
    b.className = "stat " + (bal.difference === 0 ? "good" : "warn");
  } else {
    b.style.display = "none";
  }

  document.querySelectorAll("#lang-toggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.loc === getLocale()));
}

function renderExec() {
  const s = state.manifest.summary || {};
  const list = state.list;
  const reportCount = s.report_count
    ?? list.filter((f) => f.disposition === "report").length;
  const obsCount = s.observation_count
    ?? list.filter((f) => f.disposition === "observation").length;
  const flagged = s.flagged_amount_total != null ? s.flagged_amount_total
    : list.filter((f) => f.disposition === "report")
      .reduce((a, f) => a + (f.amount_eur || 0), 0);
  const quar = s.quarantine_count
    ?? list.filter((f) => f.disposition === "quarantine").length;
  const cit = s.citations || {};
  const citPct = cit.resolvability_pct != null ? pct(cit.resolvability_pct) : "—";

  const items = [
    { v: String(reportCount), l: t("exec_key_findings"), cls: "exec-strong" },
    { v: eur(flagged, { whole: true, dashZero: false }), l: t("exec_flagged"), cls: "exec-strong" },
    { v: String(obsCount), l: t("exec_observations"), cls: "" },
    { v: citPct, l: t("exec_citations"), cls: cit.resolvability_pct === 100 ? "exec-ok" : "" },
    { v: String(quar), l: t("exec_quarantine"), cls: quar > 0 ? "exec-warn" : "exec-ok" },
  ];
  $("exec-strip").innerHTML = items.map((it) => `
    <div class="exec-item ${it.cls}">
      <span class="exec-val">${esc(it.v)}</span>
      <span class="exec-label">${esc(it.l)}</span>
    </div>`).join('<span class="exec-sep" aria-hidden="true"></span>');
}

// ============================================================ findings list ==
function renderList() {
  const host = $("findings-list");
  const list = state.list;
  const report = list.filter((f) => f.disposition === "report")
    .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const obs = list.filter((f) => f.disposition === "observation");
  const quar = list.filter((f) => f.disposition === "quarantine");
  const rej = list.filter((f) => f.disposition === "rejected");

  let html = `<div class="list-section-title key-head">${esc(t("section_key_findings"))} <span class="sec-count">${report.length}</span></div>`;
  html += report.map((f, i) => keyFindingItem(f, i + 1)).join("");

  if (obs.length) html += observationsBlock(obs);

  if (quar.length) {
    html += `<div class="list-section-title">${esc(t("section_quarantine"))} <span class="sec-count">${quar.length}</span></div>`;
    html += quar.map(obsItem).join("");
  }
  if (rej.length) {
    html += `<div class="list-section-title">${esc(t("section_rejected"))} <span class="sec-count">${rej.length}</span></div>`;
    html += rej.map(obsItem).join("");
  }
  host.innerHTML = html;
  host.querySelectorAll("[data-fid]").forEach((el) =>
    el.addEventListener("click", () => selectFinding(el.dataset.fid)));
  markActive();
}

function keyFindingItem(f, fallbackRank) {
  const rank = String(f.rank ?? fallbackRank).padStart(2, "0");
  return `<button class="key-item" data-fid="${esc(f.finding_id)}">
    <span class="key-rank">${esc(rank)}</span>
    <span class="key-body">
      <span class="key-top">
        <span class="key-scheme">${esc(label("scheme", f.scheme))}</span>
        <span class="key-amount">${esc(eur(f.amount_eur, { whole: true }))}</span>
      </span>
      <span class="key-gist">${esc(f.title)}</span>
    </span>
  </button>`;
}

function observationsBlock(obs) {
  const bySch = {};
  for (const f of obs) (bySch[f.scheme] ||= []).push(f);
  const groups = Object.entries(bySch).sort((a, b) => b[1].length - a[1].length);
  const breakdown = groups
    .map(([sch, items]) => `${label("scheme", sch)} (${items.length})`).join(" · ");
  const inner = groups.map(([sch, items]) => `
    <div class="obs-group-title">${esc(label("scheme", sch))} <span class="sec-count">${items.length}</span></div>
    ${items.map(obsItem).join("")}`).join("");
  return `<details class="obs-details">
    <summary class="obs-summary">
      <span class="obs-summary-title">${esc(t("section_observations"))} <span class="sec-count">${obs.length}</span></span>
      <span class="obs-breakdown">${esc(breakdown)}</span>
    </summary>
    <div class="obs-body">${inner}</div>
  </details>`;
}

function obsItem(f) {
  return `<button class="obs-item" data-fid="${esc(f.finding_id)}">
    <span class="obs-id">${esc(f.finding_id)}</span>
    <span class="obs-title">${esc(f.title)}</span>
    <span class="obs-amount">${esc(eur(f.amount_eur, { whole: true }))}</span>
  </button>`;
}

function renderPbc() {
  const queue = state.manifest.summary?.pbc_queue || [];
  const host = $("pbc-block");
  const total = queue.reduce((n, q) => n + (q.requests?.length || 0), 0);
  if (!total) { host.innerHTML = ""; return; }
  host.innerHTML =
    `<details class="pbc-details">
      <summary class="list-section-title">${esc(tf("pbc_title", { n: total }))}</summary>
      <div class="pbc-body">` +
    queue.map((q) => `<div class="pbc-item">
      <span class="obs-id">${esc(q.finding_id)}</span>
      <ul>${(q.requests || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    </div>`).join("") +
    `</div></details>`;
}

function markActive() {
  document.querySelectorAll("[data-fid]").forEach((el) =>
    el.classList.toggle("active", el.dataset.fid === state.currentId));
}

// ============================================================= evidence card ==
async function selectFinding(id) {
  state.currentId = id;
  markActive();
  let f = state.detail[id];
  if (!f) {
    f = await api(`/api/findings/${encodeURIComponent(id)}`);
    state.detail[id] = f;
  }
  renderCard(f);
}

function renderCard(f) {
  const el = $("evidence-card");
  el.className = "evidence-card";
  el.innerHTML = cardHtml(f);
  el.querySelectorAll(".cite").forEach((c) =>
    c.addEventListener("click", () =>
      showSource(c.dataset.sid, c.dataset.quote, c.dataset.precision, c)));
}

function cardHtml(f) {
  const claims = f.claims || [];
  const core = claims.filter((c) => c.role === "core");
  const supp = claims.filter((c) => c.role !== "core");
  const withFormula = claims.filter((c) => (c.value || {}).formula);
  const rank = f.rank != null ? String(f.rank).padStart(2, "0") : null;
  const checks = (f.innocence_checked || []).length;
  const nCounter = (f.counterevidence || []).length;
  const counterText = nCounter === 0
    ? t("defense_no_counter") : tf("defense_counter_n", { n: nCounter });

  return `
  <header class="ec-header">
    <div class="ec-eyebrow">
      ${rank ? `<span class="ec-rank">${esc(rank)}</span>` : ""}
      <span class="ec-fid">${esc(f.finding_id)}</span>
      <span class="ec-entity">${esc(f.entity_label || f.entity_key || "")}</span>
    </div>
    <div class="ec-title-row">
      <h1 class="ec-title">${esc(f.title || "")}</h1>
      <div class="ec-amount">${esc(eur(f.amount_eur, { whole: true }))}
        <small>${esc(t("card_amount_note"))}</small></div>
    </div>
    <div class="ec-badges">
      <span class="badge badge-${esc(f.disposition)}">${esc(label("disposition", f.disposition))}</span>
      <span class="tag tag-scheme">${esc(label("scheme", f.scheme))}</span>
      <span class="tag">${esc(t("meta_anomaly"))}: ${esc(f.anomaly_type)}</span>
      <span class="tag">${esc(t("meta_status"))}: ${esc(f.reporting_status)}</span>
    </div>
  </header>

  <section class="ec-block">
    <h2 class="ec-h">${esc(t("card_summary"))}</h2>
    <p class="ec-desc">${esc(f.description_llm || f.description || "")}${f.description_llm ? `<span class="llmtag">${esc(t("llm_tag"))}</span>` : ""}</p>
    ${(f.next_steps_llm && f.next_steps_llm.length) ? `<div class="nextsteps"><span class="ns-h">${esc(t("llm_next_steps"))}</span><ul>${f.next_steps_llm.slice(0,3).map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
  </section>

  <section class="ec-block">
    <h2 class="ec-h">${esc(t("card_key_evidence"))}</h2>
    ${core.map(claimHtml).join("") || `<div class="ec-note">—</div>`}
    ${supp.length ? `
    <details class="supp-details">
      <summary class="supp-summary">
        <span class="d-show">${esc(tf("card_supporting_show", { n: supp.length }))}</span>
        <span class="d-hide">${esc(t("card_supporting_hide"))}</span>
      </summary>
      <div class="supp-body">${supp.map(claimHtml).join("")}</div>
    </details>` : ""}
  </section>

  <section class="ec-block">
    <h2 class="ec-h">${esc(t("card_verification"))}</h2>
    ${withFormula.length ? withFormula.map(verifyRow).join("")
      : `<div class="ec-note">${esc(t("card_verification_none"))}</div>`}
  </section>

  <section class="ec-block">
    <h2 class="ec-h">${esc(t("card_defense"))}</h2>
    <div class="defense-line">${esc(tf("defense_line", { checks, counter: counterText }))}</div>
    <div class="defense-note">${esc(t("defense_note"))}</div>
    ${(checks || nCounter) ? `
    <details class="def-details">
      <summary class="def-summary">${esc(t("defense_show"))}</summary>
      <div class="def-body">${defenseLog(f)}</div>
    </details>` : ""}
  </section>

  <section class="ec-block">
    <h2 class="ec-h">${esc(t("card_evidence_status"))}</h2>
    ${evidenceStatusHtml(f)}
    <div class="legend"><span class="legend-label">${esc(t("legend"))}:</span>
      <span class="chip ev-not_provided_in_materials">${esc(label("evstate", "not_provided_in_materials"))}</span>
      <span class="chip ev-parse_failure">${esc(label("evstate", "parse_failure"))}</span>
      <span class="chip ev-provided_but_mismatched">${esc(label("evstate", "provided_but_mismatched"))}</span>
    </div>
    ${(f.pbc_requests || []).length ? `
    <div class="ec-subh">${esc(t("card_pbc"))}</div>
    <ul class="plain">${f.pbc_requests.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
  </section>

  <footer class="ec-footer">
    <div class="ec-foot-row"><span class="foot-key">${esc(t("meta_denominators"))}</span>${denomInline(f)}</div>
    <div class="ec-foot-row ec-foot-tags">
      <span class="tag tag-muted">${esc(t("meta_mechanism"))}: ${esc(f.mechanism || "—")}</span>
      <span class="tag tag-muted">${esc(t("meta_anomaly"))}: ${esc(f.anomaly_type || "—")}</span>
      <span class="tag tag-muted">${esc(t("meta_parser"))}: ${esc(f.parser_coverage || "—")}</span>
      <span class="tag tag-muted">${esc(t("meta_finding"))}: ${esc(f.finding_id)}</span>
      <span class="tag tag-muted">llm_used: ${f.llm_used ? "true" : "false"}</span>
    </div>
  </footer>`;
}

function claimHtml(c) {
  const v = c.value || {};
  let fig = "";
  if (v.value != null && (v.kind === "amount" || v.kind === "count")) {
    const shown = v.kind === "amount"
      ? eur(v.value, { dashZero: false })
      : `${num(v.value)} ${v.currency_or_unit || ""}`.trim();
    fig = `<span class="claim-figure">${esc(shown)}</span>`;
  }
  return `<div class="claim">
    <div class="claim-head">
      <span class="chip chip-${esc(c.verdict)}">${esc(label("verdict", c.verdict))}</span>
      <span class="tag">${esc(c.type)}</span>
      ${fig}
      <span class="claim-id">${esc(c.claim_id)}</span>
    </div>
    <div class="claim-assertion">${esc(c.assertion)}</div>
    <div class="cites">${(c.citations || []).map(citeChip).join("")}</div>
  </div>`;
}

function verifyRow(c) {
  const v = c.value;
  const rc = recompute(v.formula);
  const match = rc != null && v.value != null && Math.abs(rc - v.value) < 0.005;
  const shown = v.kind === "amount"
    ? eur(v.value, { dashZero: false })
    : `${num(v.value)} ${v.currency_or_unit || ""}`.trim();
  return `<div class="verify-row">
    <div class="verify-head">
      <span class="claim-id">${esc(c.claim_id)}</span>
      <span class="tag">${esc(c.type)}</span>
      <span class="verify-val">${esc(shown)}</span>
    </div>
    <div class="verify-formula">${esc(v.formula)}</div>
    ${rc != null ? `<div class="${match ? "recheck-ok" : "recheck-bad"}">
      ${esc(t("card_ui_recompute"))}: ${esc(v.kind === "amount" ? eur(rc, { dashZero: false }) : num(rc))}
      — ${match ? esc(t("card_recompute_ok")) : esc(t("card_recompute_bad"))}</div>` : ""}
  </div>`;
}

function defenseLog(f) {
  const ic = (f.innocence_checked || []).map((c) => `
    <div class="def-item">
      <div class="def-q">${esc(c.question || c.predicate || "")}</div>
      <div class="def-r"><span class="def-res def-res-${esc(c.result)}">${esc(c.result)}</span>
        <span class="def-reason">${esc(c.reason || c.detail || "")}</span></div>
    </div>`).join("");
  const ce = (f.counterevidence || []).map((c) => {
    const txt = typeof c === "string" ? c : (c.description || c.kind || JSON.stringify(c));
    return `<div class="def-item def-counter"><div class="def-r">${esc(txt)}</div></div>`;
  }).join("");
  const body = ic + (ce ? `<div class="def-subhead">counterevidence</div>${ce}` : "");
  return body || `<div class="ec-note">${esc(t("defense_no_log"))}</div>`;
}

function evidenceStatusHtml(f) {
  let rows = [];
  const es = f.evidence_status;
  if (Array.isArray(es) && es.length) {
    rows = es.map((e) => {
      const st = normEvState(e.state);
      return `<div class="ev-row">
        <span class="chip ev-${esc(st)}">${esc(label("evstate", st))}</span>
        <span class="ev-item">${esc(e.item)}</span></div>`;
    });
  } else {
    const missing = (f.missing_evidence || []).map((m) => typeof m === "string"
      ? { item: m, state: "not_provided_in_materials" }
      : { item: m.item, state: normEvState(m.status || m.state) });
    rows = missing.map((m) => {
      const st = normEvState(m.state);
      return `<div class="ev-row">
        <span class="chip ev-${esc(st)}">${esc(label("evstate", st))}</span>
        <span class="ev-item">${esc(m.item)}</span></div>`;
    });
  }
  return rows.join("") || `<div class="ec-note">${esc(t("card_evidence_none"))}</div>`;
}

function denomInline(f) {
  const d = f.denominators || {};
  const keys = Object.keys(d);
  if (!keys.length) return `<span class="ec-note">—</span>`;
  return keys.map((k) => `<span class="denom">
    <span class="denom-k">${esc(dnLabel(k))}</span>
    <span class="denom-v">${esc(num(d[k]))}</span></span>`).join("");
}

function dnLabel(k) {
  const v = t("dn_" + k);
  return v === "dn_" + k ? k : v;
}

function citeChip(cit, i) {
  const short = (cit.file || "").split("/").pop();
  const loc = cit.kind === "cell"
    ? tf("cite_rows", { v: (cit.row_ids || []).map((r) => r + 1).join(",") })
    : tf("cite_page", { v: cit.page_label || (cit.page_index != null ? cit.page_index + 1 : "?") });
  const title = cit.kind === "cell"
    ? `${cit.col || ""}: ${cit.cell_value ?? ""}` : (cit.quote || "");
  return `<button class="cite" data-cite="${i}"
    data-sid="${esc(cit.source_id)}"
    data-quote="${esc(cit.quote || "")}"
    data-precision="${esc(cit.locator_precision || "")}"
    title="${esc(title)}">${esc(short)} · ${esc(loc)}</button>`;
}

// ============================================================= source viewer ==
async function showSource(sourceId, quote, precision, chip) {
  document.querySelectorAll(".cite.active").forEach((x) => x.classList.remove("active"));
  if (chip) chip.classList.add("active");
  const cap = $("src-caption");
  const body = $("src-body");
  cap.dataset.loaded = "1";
  state.sourceLoaded = true;
  cap.textContent = t("src_loading");
  body.innerHTML = `<div class="empty-state">${esc(t("src_loading"))}</div>`;
  try {
    const qs = new URLSearchParams();
    if (quote) qs.set("quote", quote);
    if (precision) qs.set("precision", precision);
    const r = await api(`/api/source/${encodeURIComponent(sourceId)}/render?${qs}`);
    cap.textContent = `${r.caption || r.file}  [${sourceId}]`;
    if (r.kind === "page") {
      const markNote = r.highlight ? t("src_marked") : t("src_unmarked");
      body.innerHTML = `
        <div class="src-precision">${esc(tf("src_precision", { precision: r.locator_precision }))}
          (${esc(markNote)}) · ${esc(tf("src_page", { p: r.page_label }))}</div>
        <img alt="${esc(r.file)}" src="data:image/png;base64,${r.png_base64}">`;
    } else {
      body.innerHTML = r.html;
    }
  } catch (e) {
    cap.textContent = t("src_error_caption");
    body.innerHTML = `<div class="empty-state">${esc(tf("src_error_body", { msg: e.message }))}</div>`;
  }
}

function renderSourceHint() {
  const cap = $("src-caption");
  const hint = $("src-hint");
  if (cap && !cap.dataset.loaded) cap.textContent = t("src_hint_caption");
  if (hint) hint.textContent = t("src_hint_body");
}

// ===================================================================== go ===
init();
