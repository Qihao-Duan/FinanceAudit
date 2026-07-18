// Formatting + API helpers (ES module, no dependencies).

const EUR = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function eur(v) {
  return v == null ? "–" : EUR.format(v) + " EUR";
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

export const DISPO_LABEL = {
  report: "Bericht / report",
  observation: "Beobachtung / observation",
  quarantine: "Quarantäne / quarantine",
  rejected: "verworfen / rejected",
};

export const VERDICT_LABEL = {
  supported: "belegt / supported",
  contradicted: "widerlegt / contradicted",
  unverifiable: "nicht verifizierbar / unverifiable",
};

export const EVSTATE_LABEL = {
  not_provided: "nicht im Dossier · 材料中未提供",
  parse_failed: "Parse-Fehler · 解析失败",
  provided_no_match: "vorhanden, ohne Treffer · 已提供但不匹配",
};

export const SCHEME_LABEL = {
  fictitious_vendor: "Kreditor-Kontrollumfeld",
  threshold_splitting: "Schwellenwert-Splitting",
  expense_capitalization: "Aktivierung von Aufwand",
  cutoff: "Periodenabgrenzung",
  related_party: "Nahestehende Unternehmen",
  controls_breach: "Kontrolllücke",
  revenue_timing: "Umsatzabgrenzung",
  estimate_manipulation: "Schätzungsänderung",
};

// Recompute a claim formula in the UI when it is plain arithmetic.
// The pipeline is the authority; this is an additional client-side check
// on the literal operands embedded in the formula string.
export function recompute(formula) {
  if (!formula) return null;
  // comparison formulas (thresholds) are not numerically recomputable here
  if (/[<>]/.test(formula)) return null;
  const rhs = formula.includes("=") ? formula.split("=").pop() : formula;
  if (!/^[\d\s+\-*/().]+$/.test(rhs.trim()) || !/\d/.test(rhs)) return null;
  try {
    // eslint-disable-next-line no-new-func
    const v = Function(`"use strict"; return (${rhs});`)();
    return Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
  } catch {
    return null;
  }
}
