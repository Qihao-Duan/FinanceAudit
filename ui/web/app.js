// FinanceAudit three-pane UI. Left: findings + PBC queue. Center: evidence
// card. Right: source viewer (table fragment or rendered PDF page).
import {
  api, esc, eur, recompute,
  DISPO_LABEL, VERDICT_LABEL, EVSTATE_LABEL, SCHEME_LABEL,
} from "./fmt.js";

const $ = (id) => document.getElementById(id);
let currentFindingId = null;

// ---------------------------------------------------------------- top bar --
async function loadTop() {
  const [status, manifest] = await Promise.all([api("/api/status"), api("/api/manifest")]);
  const s = manifest.summary || {};
  $("stat-mode").textContent = status.mode === "fixtures"
    ? "Fixture-Modus (Pipeline-Artefakte fehlen)" : "Pipeline-Daten (build/)";
  $("stat-mode").classList.add(status.mode === "fixtures" ? "warn" : "good");
  $("stat-coverage").textContent =
    `Parse-Abdeckung ${s.parse_coverage_pct != null ? s.parse_coverage_pct + " %" : "–"} · ` +
    `${s.n_files_complete}/${s.n_files} Dateien`;
  if ((s.parse_coverage_pct ?? 0) >= 100) $("stat-coverage").classList.add("good");
  const bal = manifest.gl_balance;
  if (bal) {
    $("stat-balance").textContent =
      `HB Soll=Haben, Differenz ${new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2 }).format(bal.difference)}`;
    $("stat-balance").classList.add(bal.difference === 0 ? "good" : "warn");
  }
  $("stat-quarantine").textContent = `Quarantäne ${s.quarantine_count ?? 0}`;
  if ((s.quarantine_count ?? 0) > 0) $("stat-quarantine").classList.add("warn");
  renderPbc(s.pbc_queue || []);
}

// ------------------------------------------------------------ findings list --
const SECTIONS = [
  ["report", "Berichtete Feststellungen / reported"],
  ["observation", "Beobachtungen / observations"],
  ["quarantine", "Quarantäne — nicht verifizierbar / quarantine"],
  ["rejected", "Verworfen / rejected"],
];

async function loadFindings() {
  const list = await api("/api/findings");
  const host = $("findings-list");
  host.innerHTML = "";
  for (const [dispo, label] of SECTIONS) {
    const items = list.filter((f) => f.disposition === dispo);
    if (!items.length) continue;
    const h = document.createElement("div");
    h.className = "list-section-title";
    h.textContent = `${label} (${items.length})`;
    host.appendChild(h);
    for (const f of items) host.appendChild(findingButton(f));
  }
  if (list.length) selectFinding(list[0].finding_id);
}

function findingButton(f) {
  const b = document.createElement("button");
  b.className = "finding-item";
  b.id = `fi-${f.finding_id}`;
  b.innerHTML = `
    <div class="fi-top">
      <span class="fi-id">${esc(f.finding_id)}</span>
      <span class="fi-amount">${esc(eur(f.amount_eur))}</span>
    </div>
    <div class="fi-title">${esc(f.title)}</div>
    <div class="fi-tags">
      <span class="badge badge-${esc(f.disposition)}">${esc(DISPO_LABEL[f.disposition] || f.disposition)}</span>
      <span class="tag">${esc(SCHEME_LABEL[f.scheme] || f.scheme)}</span>
      <span class="tag">Kernaussagen ${f.core_supported}/${f.core_total} belegt</span>
    </div>`;
  b.addEventListener("click", () => selectFinding(f.finding_id));
  return b;
}

function renderPbc(queue) {
  const host = $("pbc-block");
  const total = queue.reduce((n, q) => n + q.requests.length, 0);
  host.innerHTML = `<div class="list-section-title">Nachforderungen an den Mandanten · PBC / 需补资料 (${total})</div>`;
  for (const q of queue) {
    const d = document.createElement("div");
    d.className = "pbc-item";
    d.innerHTML = `<span class="fi-id">${esc(q.finding_id)}</span>
      <ul>${q.requests.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
    host.appendChild(d);
  }
}

// ------------------------------------------------------------ evidence card --
async function selectFinding(id) {
  currentFindingId = id;
  document.querySelectorAll(".finding-item").forEach((el) =>
    el.classList.toggle("active", el.id === `fi-${id}`));
  const f = await api(`/api/findings/${encodeURIComponent(id)}`);
  $("evidence-card").className = "";
  $("evidence-card").innerHTML = evidenceCardHtml(f);
  bindCitations(f);
}

function evidenceCardHtml(f) {
  const core = (f.claims || []).filter((c) => c.role === "core");
  const supp = (f.claims || []).filter((c) => c.role !== "core");
  const nChecks = (f.innocence_checked || []).filter((c) => c.checked).length;
  const noCounter = (f.counterevidence || []).length === 0;
  return `
  <header class="ec-header">
    <div class="ec-title-row">
      <div>
        <div class="fi-id">${esc(f.finding_id)} · ${esc(f.entity_label || f.entity_key || "")}</div>
        <h1 class="ec-title">${esc(f.title || "")}</h1>
      </div>
      <div class="ec-amount">${esc(eur(f.amount_eur))}<small>Betrag lt. Neuberechnung</small></div>
    </div>
    <div class="ec-meta">
      <span class="badge badge-${esc(f.disposition)}">${esc(DISPO_LABEL[f.disposition] || f.disposition)}</span>
      <span class="tag">Schema: ${esc(SCHEME_LABEL[f.scheme] || f.scheme)}</span>
      <span class="tag">Anomalie: ${esc(f.anomaly_type)}</span>
      <span class="tag">Status: ${esc(f.reporting_status)}</span>
      <span class="tag">Parser: ${esc(f.parser_coverage)}</span>
      <span class="tag">llm_used: ${f.llm_used ? "true" : "false"}</span>
    </div>
  </header>
  <p class="ec-desc">${esc(f.description || "")}</p>

  <div class="ec-section-title">Aussagenbaum / assertion tree — Kernaussagen (${core.length})</div>
  ${core.map(claimHtml).join("")}
  ${supp.length ? `<div class="ec-section-title">Stützende Aussagen / supporting (${supp.length})</div>` : ""}
  ${supp.map(claimHtml).join("")}

  <div class="ec-section-title">Entlastungsprüfung / defense log</div>
  ${(f.innocence_checked || []).map((c) => `
    <div class="defense-item">
      <div class="defense-pred">${esc(c.predicate)}</div>
      <div class="defense-res">${esc(c.result)}</div>
    </div>`).join("") || '<div class="ev-note">Keine Entlastungsprüfungen protokolliert.</div>'}
  ${(f.counterevidence || []).length
    ? `<ul class="plain">${f.counterevidence.map((c) => `<li>${esc(typeof c === "string" ? c : c.item || JSON.stringify(c))}</li>`).join("")}</ul>`
    : `<div class="defense-note">Es wurden ${nChecks} Entlastungsprüfungen durchgeführt; ${noCounter ? "kein Gegenbeweis gefunden" : "siehe Gegenbeweise oben"}. Dies ist keine Schuldaussage — fehlende Entlastung ist kein Belastungsbeweis.</div>`}

  <div class="ec-section-title">Erwartete &amp; fehlende Nachweise / evidence status</div>
  ${evidenceStatusHtml(f)}
  <div class="legend">Legende:
    <span class="chip ev-not_provided">${esc(EVSTATE_LABEL.not_provided)}</span>
    <span class="chip ev-parse_failed">${esc(EVSTATE_LABEL.parse_failed)}</span>
    <span class="chip ev-provided_no_match">${esc(EVSTATE_LABEL.provided_no_match)}</span>
  </div>

  <div class="ec-section-title">Grundgesamtheiten / denominators</div>
  <table class="kv-table"><tbody>
    ${Object.entries(f.denominators || {}).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
  </tbody></table>

  ${(f.pbc_requests || []).length ? `
  <div class="ec-section-title">Nachforderungen / PBC requests</div>
  <ul class="plain">${f.pbc_requests.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
  `;
}

function evidenceStatusHtml(f) {
  const missing = (f.missing_evidence || []).map((m) =>
    typeof m === "string" ? { item: m, status: "not_provided", note: "" } : m);
  const provided = (f.expected_evidence || []).filter((e) => {
    return !missing.some((m) => m.item.includes(e) || e.includes(m.item));
  });
  const rows = [];
  for (const m of missing) {
    rows.push(`<div class="ev-row">
      <span class="chip ev-${esc(m.status || "not_provided")}">${esc(EVSTATE_LABEL[m.status] || m.status)}</span>
      <span>${esc(m.item)}</span>
      ${m.note ? `<span class="ev-note">— ${esc(m.note)}</span>` : ""}</div>`);
  }
  for (const e of provided) {
    rows.push(`<div class="ev-row"><span class="chip tag">erwartet</span><span>${esc(e)}</span></div>`);
  }
  return rows.join("") || '<div class="ev-note">Keine offenen Nachweispositionen.</div>';
}

function claimHtml(c) {
  const v = c.value;
  let valueBlock = "";
  if (v && v.formula) {
    const rc = recompute(v.formula);
    const match = rc != null && v.value != null && Math.abs(rc - v.value) < 0.005;
    valueBlock = `
      <div class="claim-value">
        <span class="val">${esc(v.kind === "amount" ? eur(v.value) : `${v.value} ${v.currency_or_unit || ""}`)}</span>
        &nbsp;=&nbsp; ${esc(v.formula)}
        ${rc != null ? `<br><span class="${match ? "recheck-ok" : "recheck-bad"}">UI-Neuberechnung: ${esc(v.kind === "amount" ? eur(rc) : rc)} — ${match ? "stimmt überein" : "WEICHT AB"}</span>` : ""}
      </div>`;
  }
  return `
  <div class="claim">
    <div class="claim-head">
      <span class="chip chip-${c.role === "core" ? "core" : "supporting"}">${c.role === "core" ? "Kern" : "stützend"}</span>
      <span class="tag">${esc(c.type)}</span>
      <span class="chip chip-${esc(c.verdict)}">${esc(VERDICT_LABEL[c.verdict] || c.verdict)}</span>
      <span class="claim-id">${esc(c.claim_id)}</span>
    </div>
    <div class="claim-assertion">${esc(c.assertion)}</div>
    ${valueBlock}
    <div class="cites">${(c.citations || []).map(citeChip).join("")}</div>
  </div>`;
}

function citeChip(cit, i) {
  const short = (cit.file || "").split("/").pop();
  const loc = cit.kind === "cell"
    ? `Z. ${(cit.row_ids || []).map((r) => r + 1).join(",")}`
    : `S. ${cit.page_label || (cit.page_index != null ? cit.page_index + 1 : "?")}`;
  return `<button class="cite" data-cite="${i}"
    data-sid="${esc(cit.source_id)}"
    data-quote="${esc(cit.quote || "")}"
    data-precision="${esc(cit.locator_precision || "")}"
    title="${esc(cit.kind === "cell" ? `${cit.col}: ${cit.cell_value}` : cit.quote || "")}">
    ${esc(short)} · ${esc(loc)}</button>`;
}

function bindCitations() {
  document.querySelectorAll(".cite").forEach((el) => {
    el.addEventListener("click", () => {
      document.querySelectorAll(".cite.active").forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      showSource(el.dataset.sid, el.dataset.quote, el.dataset.precision);
    });
  });
}

// ------------------------------------------------------------ source viewer --
async function showSource(sourceId, quote, precision) {
  const cap = $("src-caption");
  const body = $("src-body");
  cap.textContent = "lädt …";
  body.innerHTML = '<div class="empty-state">Quelle wird geladen …</div>';
  try {
    const qs = new URLSearchParams();
    if (quote) qs.set("quote", quote);
    if (precision) qs.set("precision", precision);
    const r = await api(`/api/source/${encodeURIComponent(sourceId)}/render?${qs}`);
    cap.textContent = `${r.caption || r.file}  [${sourceId}]`;
    if (r.kind === "page") {
      body.innerHTML = `
        <div class="src-precision">Fundstelle: ${esc(r.locator_precision)}${r.highlight ? " (markiert)" : " (Seite ohne Markierung)"} · Seite ${esc(r.page_label)}</div>
        <img alt="Seitenansicht ${esc(r.file)}" src="data:image/png;base64,${r.png_base64}">`;
    } else {
      body.innerHTML = r.html;
    }
  } catch (e) {
    cap.textContent = "Fehler";
    body.innerHTML = `<div class="empty-state">Quelle nicht ladbar: ${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------- go --
loadTop().catch((e) => { $("stat-mode").textContent = `Status-Fehler: ${e.message}`; });
loadFindings().catch((e) => {
  $("findings-list").innerHTML = `<div class="empty-state">Feststellungen nicht ladbar: ${esc(e.message)}</div>`;
});
