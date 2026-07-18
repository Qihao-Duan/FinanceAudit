// FinanceAudit — i18n + formatting helpers (ES module, no dependencies).
// English is the default UI language; German is available via the top-bar
// toggle and persists in localStorage. Pipeline-generated content
// (assertions, narratives) is already English and is never translated here.

// ------------------------------------------------------------------ locale --
const LOCALE_KEY = "fa.locale";
const LOCALES = ["en", "de"];
let _locale = null;

export function getLocale() {
  if (_locale) return _locale;
  try {
    const saved = localStorage.getItem(LOCALE_KEY);
    _locale = LOCALES.includes(saved) ? saved : "en";
  } catch {
    _locale = "en";
  }
  return _locale;
}

export function setLocale(loc) {
  _locale = LOCALES.includes(loc) ? loc : "en";
  try { localStorage.setItem(LOCALE_KEY, _locale); } catch { /* ignore */ }
  return _locale;
}

// ------------------------------------------------------------- chrome text --
// Every string of UI chrome lives here. `{name}` placeholders are filled by tf().
const STR = {
  en: {
    brand_sub: "Muster Verpackungen GmbH · FY 2025 · GDPdU data-carrier disclosure",
    mode_build: "Pipeline data (build/)",
    mode_fixtures: "Fixture mode (pipeline artefacts missing)",
    mode_error: "Status error: {msg}",
    coverage: "Parse coverage {pct} · {done}/{total} files",
    gl_balance: "GL debit = credit, Δ {diff}",
    lang_label: "Language",

    // executive summary strip
    exec_key_findings: "key findings",
    exec_flagged: "flagged amount",
    exec_observations: "observations",
    exec_citations: "citations resolvable",
    exec_quarantine: "quarantine",

    // left pane
    section_key_findings: "Key findings",
    section_observations: "Observations",
    section_quarantine: "Quarantine",
    section_rejected: "Rejected",
    list_core: "{s}/{t} core supported",
    pbc_title: "Client requests · PBC ({n})",
    findings_error: "Findings could not be loaded: {msg}",
    footer_llm: "llm_used: false · deterministic pipeline",

    // evidence card
    card_pick: "Select a finding …",
    cite_rows: "row {v}",
    cite_page: "p. {v}",
    card_amount_note: "amount per recomputation",
    card_summary: "Summary",
    llm_tag: "AI-drafted narrative (facts locked, post-filtered)",
    llm_next_steps: "Suggested next steps (AI-drafted)",
    card_key_evidence: "Key evidence",
    card_supporting_show: "Show {n} supporting claims",
    card_supporting_hide: "Hide supporting claims",
    card_verification: "Verification",
    card_verification_none: "No recomputable formula in this finding.",
    card_ui_recompute: "UI recompute",
    card_recompute_ok: "matches",
    card_recompute_bad: "DIFFERS",
    card_defense: "Defense check",
    defense_no_counter: "no counterevidence found",
    defense_counter_n: "{n} counterevidence item(s)",
    defense_line: "{checks} innocence checks · {counter}",
    defense_note: "Absence of exculpation is not inculpatory evidence.",
    defense_show: "Show defense log",
    defense_no_log: "No innocence checks recorded.",
    card_evidence_status: "Evidence status / PBC",
    card_pbc: "Client requests (PBC)",
    card_evidence_none: "No open evidence items.",
    legend: "Legend",
    card_details: "Details",
    meta_scheme: "Scheme",
    meta_mechanism: "Mechanism",
    meta_anomaly: "Anomaly",
    meta_status: "Status",
    meta_parser: "Parser",
    meta_entity: "Entity",
    meta_finding: "Finding",
    meta_denominators: "Denominators",
    dn_population_size: "Population size",
    dn_rule_hits: "Rule hits",
    dn_peers_with_expected_evidence: "Peers with expected evidence",
    dn_defender_rejected_hits: "Defender-rejected hits",

    // source viewer
    src_hint_caption: "Source view — click a citation in the evidence card",
    src_hint_body: "Each citation opens its original source here: table rows as an excerpt with the cited line highlighted, PDF pages as an image with the passage marked.",
    src_loading: "Loading source …",
    src_precision: "Location: {precision}",
    src_marked: "highlighted",
    src_unmarked: "page, no highlight",
    src_page: "page {p}",
    src_error_caption: "Error",
    src_error_body: "Source not loadable: {msg}",
  },
  de: {
    brand_sub: "Muster Verpackungen GmbH · GJ 2025 · GDPdU-Datenträgerüberlassung",
    mode_build: "Pipeline-Daten (build/)",
    mode_fixtures: "Fixture-Modus (Pipeline-Artefakte fehlen)",
    mode_error: "Status-Fehler: {msg}",
    coverage: "Parse-Abdeckung {pct} · {done}/{total} Dateien",
    gl_balance: "HB Soll = Haben, Δ {diff}",
    lang_label: "Sprache",

    exec_key_findings: "Kernfeststellungen",
    exec_flagged: "markierter Betrag",
    exec_observations: "Beobachtungen",
    exec_citations: "Belege auflösbar",
    exec_quarantine: "Quarantäne",

    section_key_findings: "Kernfeststellungen",
    section_observations: "Beobachtungen",
    section_quarantine: "Quarantäne",
    section_rejected: "Verworfen",
    list_core: "{s}/{t} Kernaussagen belegt",
    pbc_title: "Nachforderungen · PBC ({n})",
    findings_error: "Feststellungen nicht ladbar: {msg}",
    footer_llm: "llm_used: false · deterministische Pipeline",

    card_pick: "Feststellung auswählen …",
    cite_rows: "Z. {v}",
    cite_page: "S. {v}",
    card_amount_note: "Betrag lt. Neuberechnung",
    card_summary: "Zusammenfassung",
    llm_tag: "KI-formulierte Zusammenfassung (Fakten fixiert, nachgefiltert)",
    llm_next_steps: "Vorgeschlagene nächste Schritte (KI-Entwurf)",
    card_key_evidence: "Kernnachweise",
    card_supporting_show: "{n} stützende Aussagen anzeigen",
    card_supporting_hide: "Stützende Aussagen ausblenden",
    card_verification: "Nachrechnung",
    card_verification_none: "Keine nachrechenbare Formel in dieser Feststellung.",
    card_ui_recompute: "UI-Neuberechnung",
    card_recompute_ok: "stimmt überein",
    card_recompute_bad: "WEICHT AB",
    card_defense: "Entlastungsprüfung",
    defense_no_counter: "kein Gegenbeweis gefunden",
    defense_counter_n: "{n} Gegenbeweis-Position(en)",
    defense_line: "{checks} Entlastungsprüfungen · {counter}",
    defense_note: "Fehlende Entlastung ist kein Belastungsbeweis.",
    defense_show: "Entlastungsprotokoll anzeigen",
    defense_no_log: "Keine Entlastungsprüfungen protokolliert.",
    card_evidence_status: "Nachweisstatus / PBC",
    card_pbc: "Nachforderungen (PBC)",
    card_evidence_none: "Keine offenen Nachweispositionen.",
    legend: "Legende",
    card_details: "Details",
    meta_scheme: "Schema",
    meta_mechanism: "Mechanismus",
    meta_anomaly: "Anomalie",
    meta_status: "Status",
    meta_parser: "Parser",
    meta_entity: "Einheit",
    meta_finding: "Feststellung",
    meta_denominators: "Grundgesamtheiten",
    dn_population_size: "Grundgesamtheit",
    dn_rule_hits: "Regeltreffer",
    dn_peers_with_expected_evidence: "Vergleichsfälle mit Nachweis",
    dn_defender_rejected_hits: "Vom Defender verworfen",

    src_hint_caption: "Quellenansicht — Zitat in der Evidenzkarte anklicken",
    src_hint_body: "Jedes Zitat lädt hier die Originalquelle: Tabellenzeilen als Ausschnitt mit markierter Fundstelle, PDF-Seiten als Bild mit hervorgehobener Passage.",
    src_loading: "Quelle wird geladen …",
    src_precision: "Fundstelle: {precision}",
    src_marked: "markiert",
    src_unmarked: "Seite, ohne Markierung",
    src_page: "Seite {p}",
    src_error_caption: "Fehler",
    src_error_body: "Quelle nicht ladbar: {msg}",
  },
};

export function t(key) {
  const table = STR[getLocale()] || STR.en;
  if (key in table) return table[key];
  return key in STR.en ? STR.en[key] : key;
}

export function tf(key, params = {}) {
  let s = t(key);
  for (const [k, v] of Object.entries(params)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

// ----------------------------------------------------------- typed labels --
const LABELS = {
  disposition: {
    en: { report: "Reported", observation: "Observation", quarantine: "Quarantine", rejected: "Rejected" },
    de: { report: "Bericht", observation: "Beobachtung", quarantine: "Quarantäne", rejected: "Verworfen" },
  },
  verdict: {
    en: { supported: "Supported", contradicted: "Contradicted", unverifiable: "Unverifiable" },
    de: { supported: "Belegt", contradicted: "Widerlegt", unverifiable: "Nicht verifizierbar" },
  },
  role: {
    en: { core: "Core", supporting: "Supporting" },
    de: { core: "Kern", supporting: "Stützend" },
  },
  // three-state evidence status (per task): not provided / parse failure / mismatched
  evstate: {
    en: {
      not_provided_in_materials: "not provided in materials",
      parse_failure: "parse failure",
      provided_but_mismatched: "provided but mismatched",
    },
    de: {
      not_provided_in_materials: "nicht in den Unterlagen",
      parse_failure: "Parse-Fehler",
      provided_but_mismatched: "vorhanden, aber abweichend",
    },
  },
  scheme: {
    en: {
      fictitious_vendor: "Vendor control environment",
      threshold_splitting: "Threshold splitting",
      expense_capitalization: "Expense capitalization",
      cutoff: "Period cut-off",
      related_party: "Related party",
      controls_breach: "Controls breach",
      revenue_timing: "Revenue timing",
      estimate_manipulation: "Estimate change",
      round_amounts: "Round amounts",
      reconciliation: "Reconciliation",
    },
    de: {
      fictitious_vendor: "Kreditor-Kontrollumfeld",
      threshold_splitting: "Schwellenwert-Splitting",
      expense_capitalization: "Aktivierung von Aufwand",
      cutoff: "Periodenabgrenzung",
      related_party: "Nahestehende Unternehmen",
      controls_breach: "Kontrolllücke",
      revenue_timing: "Umsatzabgrenzung",
      estimate_manipulation: "Schätzungsänderung",
      round_amounts: "Runde Beträge",
      reconciliation: "Abstimmung",
    },
  },
};

// Map any legacy evidence-status state names onto the canonical three states.
const EVSTATE_ALIAS = {
  not_provided: "not_provided_in_materials",
  parse_failed: "parse_failure",
  provided_no_match: "provided_but_mismatched",
};

export function normEvState(state) {
  return EVSTATE_ALIAS[state] || state || "not_provided_in_materials";
}

export function label(kind, key) {
  const grp = LABELS[kind] || {};
  const table = grp[getLocale()] || grp.en || {};
  if (key in table) return table[key];
  const en = grp.en || {};
  if (key in en) return en[key];
  return key == null ? "" : String(key);
}

// ------------------------------------------------------------- formatting --
export function eur(v, opts = {}) {
  const { whole = false, dashZero = true } = opts;
  const n = typeof v === "number" ? v : (v == null ? null : Number(v));
  if (n == null || Number.isNaN(n) || (dashZero && n === 0)) return "—";
  const loc = getLocale() === "de" ? "de-DE" : "en-IE";
  return new Intl.NumberFormat(loc, {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(n);
}

export function num(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return new Intl.NumberFormat(getLocale() === "de" ? "de-DE" : "en-IE").format(n);
}

export function pct(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  return `${Number.isInteger(n) ? n : n.toFixed(1)} %`;
}

export function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

// Recompute a claim formula in the UI when it is plain arithmetic. The
// pipeline is the authority; this is an additional client-side check on the
// literal operands embedded in the formula string.
export function recompute(formula) {
  if (!formula) return null;
  if (/[<>]/.test(formula)) return null;               // comparisons: not recomputable
  const rhs = formula.includes("=") ? formula.split("=").pop() : formula;
  if (!/^[\d\s+\-*/().]+$/.test(rhs.trim()) || !/\d/.test(rhs)) return null;
  try {
    const v = Function(`"use strict"; return (${rhs});`)();
    return Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
  } catch {
    return null;
  }
}
