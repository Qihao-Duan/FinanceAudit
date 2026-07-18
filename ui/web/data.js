// Data Explorer — browse the structured DuckDB tables behind the findings.
// Self-contained module: owns #data-view; toggles against the findings layout.
import { esc, api, num, getLocale } from "./fmt.js";

const L = {
  en: {
    tables: "Tables", search: "Search in table …", rows: "rows",
    showing: (a, b, t) => `${a}–${b} of ${num(t)}`,
    prev: "Prev", next: "Next", profile: "Column semantics",
    constant: "constant", conflict: "conflict",
    hint: "Pick a table on the left. This is the typed data layer the pipeline " +
          "built from the dossier (semantic aliases applied); every row keeps " +
          "its source_id back to the original file.",
    findings: "Findings", data: "Data",
  },
  de: {
    tables: "Tabellen", search: "In Tabelle suchen …", rows: "Zeilen",
    showing: (a, b, t) => `${a}–${b} von ${num(t)}`,
    prev: "Zurück", next: "Weiter", profile: "Spaltensemantik",
    constant: "konstant", conflict: "Konflikt",
    hint: "Links eine Tabelle wählen. Dies ist die typisierte Datenschicht " +
          "aus dem Dossier (semantische Aliasse angewendet); jede Zeile behält " +
          "ihre source_id zur Originaldatei.",
    findings: "Feststellungen", data: "Daten",
  },
};
const t = (k) => (L[getLocale()] || L.en)[k];

const state = { tables: [], table: null, offset: 0, limit: 50, q: "", meta: null };

function el(id) { return document.getElementById(id); }

export function initDataExplorer() {
  const toggle = el("view-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-view]");
    if (btn) showView(btn.dataset.view);
  });
  el("data-search").addEventListener("input", debounce((ev) => {
    state.q = ev.target.value.trim();
    state.offset = 0;
    loadTable();
  }, 300));
  el("data-prev").addEventListener("click", () => page(-1));
  el("data-next").addEventListener("click", () => page(1));
  relabel();
}

function relabel() {
  el("view-findings-btn").textContent = t("findings");
  el("view-data-btn").textContent = t("data");
  el("data-tables-h").textContent = t("tables");
  el("data-search").placeholder = t("search");
  el("data-prev").textContent = t("prev");
  el("data-next").textContent = t("next");
  if (!state.table) el("data-body").innerHTML =
    `<div class="empty-state">${esc(t("hint"))}</div>`;
}

export function showView(which) {
  const dataOn = which === "data";
  document.querySelector("main.layout").hidden = dataOn;
  el("data-view").hidden = !dataOn;
  document.querySelectorAll("#view-toggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === which));
  relabel();
  if (dataOn && !state.tables.length) loadTables();
}

async function loadTables() {
  try {
    state.tables = await api("/api/tables");
  } catch (e) {
    el("data-tables").innerHTML = `<div class="empty-state">${esc(String(e))}</div>`;
    return;
  }
  el("data-tables").innerHTML = state.tables.map((tb) => `
    <button type="button" class="tbl-item${tb.name === state.table ? " active" : ""}"
            data-table="${esc(tb.name)}">
      <span class="tbl-name">${esc(tb.name)}</span>
      <span class="tbl-rows">${num(tb.rows)}</span>
    </button>`).join("");
  el("data-tables").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      state.table = b.dataset.table;
      state.offset = 0; state.q = ""; el("data-search").value = "";
      loadTables();  // refresh active highlight
      loadTable();
    }));
}

function page(dir) {
  if (!state.meta) return;
  const next = state.offset + dir * state.limit;
  if (next < 0 || next >= state.meta.total) return;
  state.offset = next;
  loadTable();
}

async function loadTable() {
  if (!state.table) return;
  const qs = new URLSearchParams({ limit: state.limit, offset: state.offset });
  if (state.q) qs.set("q", state.q);
  let d;
  try {
    d = await api(`/api/tables/${encodeURIComponent(state.table)}?${qs}`);
  } catch (e) {
    el("data-body").innerHTML = `<div class="empty-state">${esc(String(e))}</div>`;
    return;
  }
  state.meta = d;
  const tb = state.tables.find((x) => x.name === d.name);
  el("data-caption").textContent =
    `${d.name} · ${num(d.total)} ${t("rows")}` +
    (tb && tb.files.length ? ` · ${tb.files.join(", ")}` : "");
  const lo = d.total ? d.offset + 1 : 0;
  const hi = Math.min(d.offset + d.limit, d.total);
  el("data-range").textContent = t("showing")(num(lo), num(hi), d.total);

  const chips = (d.profiles || []).filter((p) => p.semantic_role || p.conflict ||
                                                 p.is_constant).map((p) => {
    const bits = [];
    if (p.semantic_role && p.semantic_role !== p.column)
      bits.push(`→ ${p.semantic_role}`);
    if (p.is_constant) bits.push(t("constant"));
    if (p.conflict) bits.push(`⚠ ${t("conflict")}: ${p.conflict}`);
    return bits.length
      ? `<span class="prof-chip${p.conflict ? " warn" : ""}"
              title="${esc(p.dtype)} · ${num(p.n_unique)} unique">
           <b>${esc(p.column)}</b> ${esc(bits.join(" · "))}</span>`
      : "";
  }).join("");
  el("data-profile").innerHTML = chips
    ? `<span class="prof-h">${esc(t("profile"))}:</span> ${chips}` : "";

  el("data-body").innerHTML = `
    <table class="src-table data-table"><thead><tr>
      ${d.columns.map((c) => `<th>${esc(c)}</th>`).join("")}
    </tr></thead><tbody>
      ${d.rows.map((r) => `<tr>${r.map((v) =>
        `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}
    </tbody></table>`;
}

function debounce(fn, ms) {
  let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); };
}

// re-render labels when the locale toggles (fmt.js stores it in localStorage;
// app.js reloads chrome — we just listen for the same clicks)
document.addEventListener("click", (ev) => {
  if (ev.target.closest("#lang-toggle button")) setTimeout(relabel, 0);
});

initDataExplorer();
