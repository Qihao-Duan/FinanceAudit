"""Threshold extraction for the Finder (Agent B).

Extracts audit-plan thresholds from the ingested document units (``doc_units``
table in build/audit.duckdb) and writes ``build/thresholds.json``.

Three-level fallback per PLAN §3.2:
  1. extracted   — value parsed from a dossier document, with a citation
                   (source_id + verbatim quote <= 30 words)
  2. config_default — hard-coded default used when extraction fails
  3. (threshold-free rules) — rules that need no absolute threshold keep
     working regardless; recorded in ``semantics`` only.

Deterministic, no LLM. ``llm_used`` is always false here.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import duckdb

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Config defaults (level-2 fallback). Values mirror the practice-set audit
# plan; on a new dossier the extractor runs first and overrides them.
# ---------------------------------------------------------------------------
CONFIG_DEFAULTS: Dict[str, Any] = {
    "approval_limit_eur": 10000.0,
    "materiality_overall_eur": 400000.0,
    "materiality_performance_eur": 300000.0,
    "jet_sampling_eur": 25000.0,
    "lock_date": "2026-01-20",
}

# Free parameters of the rule layer (not document-extracted; tuned defaults).
RULE_PARAMETERS: Dict[str, Any] = {
    "near_limit_epsilon_eur": 500.0,      # [L-eps, L) band for near-limit payments
    "split_window_days": 3,               # date window for same-debt split clustering
    "fast_pay_creation_days": 30,         # new-vendor: first payment within N days of creation
    "fast_pay_invoice_days": 7,           # new-vendor: invoice-to-payment <= N days
    "bank_change_pay_window_days": 30,    # payment within N days after a bank-details change
    "signature_max_support": 2,           # R8: signature support <= k counts as rare
}

# Extraction patterns: key -> list of (regex, value_kind). First match wins.
# \s+ in patterns tolerates line breaks inside PDF paragraphs.
_PATTERNS: Dict[str, list] = {
    "approval_limit_eur": [
        (r"Zahlungsfreigaben\s+ab\s+([\d.,]+)\s*EUR", "amount"),
        (r"Freigabegrenze\s*(?:von)?\s*([\d.,]+)\s*EUR", "amount"),
        (r"Vier-Augen[^\d]{0,40}([\d.,]+)\s*EUR", "amount"),
    ],
    "materiality_overall_eur": [
        (r"Gesamtwesentlichkeit\s+([\d.,]+)\s*EUR", "amount"),
        (r"overall\s+materiality[^\d]{0,20}([\d.,]+)", "amount"),
    ],
    "materiality_performance_eur": [
        (r"Toleranzwesentlichkeit\s+([\d.,]+)\s*EUR", "amount"),
        (r"performance\s+materiality[^\d]{0,20}([\d.,]+)", "amount"),
    ],
    "jet_sampling_eur": [
        (r"Nichtaufgriffsgrenze\s+JET\s+([\d.,]+)\s*EUR", "amount"),
        (r"Nichtaufgriffsgrenze\s+([\d.,]+)\s*EUR", "amount"),
    ],
    "lock_date": [
        (r"Festschreibung\s+des\s+Geschäftsjahres\s+erfolgte\s+zum\s+(\d{2}\.\d{2}\.\d{4})", "date"),
        (r"Festschreibung[^0-9]{0,120}?(\d{2}\.\d{2}\.\d{4})", "date"),
    ],
}

SEMANTICS: Dict[str, str] = {
    "split_detection_threshold": "approval_limit_eur",
    "approval_limit_eur": (
        "Payment dual-approval control limit (zweite Freigabe ab 10.000 EUR, "
        "Vier-Augen-Prinzip). This is the ONLY threshold used for "
        "split/structuring detection (R3)."
    ),
    "materiality_overall_eur": (
        "Overall materiality — evaluates aggregate misstatement of the "
        "financial statements as a whole. NEVER downgrades small qualitative "
        "findings (SoD conflicts, split payments, dangling references)."
    ),
    "materiality_performance_eur": (
        "Performance materiality — audit planning/scoping parameter only. "
        "Not a reporting filter for fraud leads."
    ),
    "jet_sampling_eur": (
        "JET sampling parameter (Nichtaufgriffsgrenze) of this audit plan — "
        "journal-entry-test selection only. Not a reporting filter."
    ),
    "lock_date": (
        "Festschreibung (GoBD lock) date of the fiscal year. Entries recorded "
        "after this date are flagged by R5."
    ),
    "note": (
        "Qualitative findings are never downgraded by any monetary threshold "
        "(ISA [DE] 240 logic). Threshold-free (relative/distribution) rules "
        "are the third fallback level when no value can be extracted."
    ),
}


def _parse_german_amount(s: str) -> Optional[float]:
    s = s.strip().rstrip(".,")
    if not s:
        return None
    # German format: '.' thousands separator, ',' decimal separator
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_german_date(s: str) -> Optional[str]:
    try:
        return datetime.strptime(s.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def _make_quote(text: str, match: "re.Match", max_words: int = 30) -> str:
    """Verbatim quote around the match, capped at max_words words."""
    flat = " ".join(text.split())
    # locate the matched literal in the flattened text
    needle = " ".join(match.group(0).split())
    pos = flat.find(needle)
    if pos < 0:
        pos = 0
    # expand to sentence-ish window around the needle
    start = flat.rfind(". ", 0, pos)
    start = start + 2 if start >= 0 else 0
    words = flat[start:].split()
    return " ".join(words[:max_words])


def extract_thresholds(con: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Scan doc_units for threshold statements. Returns key -> entry dict."""
    units = con.execute(
        """
        SELECT du.source_id, du.file, du.page_index, du.page_label, du.para_no,
               du.unit_type, du.text
        FROM doc_units du
        WHERE du.text IS NOT NULL AND length(du.text) > 0
        ORDER BY du.file, du.page_index, du.para_no
        """
    ).fetchall()

    out: Dict[str, Any] = {}
    for key, patterns in _PATTERNS.items():
        entry: Dict[str, Any] = {
            "value": CONFIG_DEFAULTS[key],
            "provenance": "config_default",
            "citation": None,
            "config_default": CONFIG_DEFAULTS[key],
        }
        found = False
        for pattern, kind in patterns:
            if found:
                break
            rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
            for sid, file, page_index, page_label, para_no, unit_type, text in units:
                m = rx.search(text)
                if not m:
                    continue
                value: Any = (
                    _parse_german_amount(m.group(1))
                    if kind == "amount"
                    else _parse_german_date(m.group(1))
                )
                if value is None:
                    continue
                entry["value"] = value
                entry["provenance"] = "extracted"
                entry["citation"] = {
                    "kind": "passage",
                    "source_id": sid,
                    "file": file,
                    "page_index": int(page_index) if page_index is not None else None,
                    "page_label": page_label,
                    "para_no": int(para_no) if para_no is not None else None,
                    "quote": _make_quote(text, m),
                    "locator_precision": "page_only",
                }
                found = True
                break
        out[key] = entry
    return out


def build_thresholds(con: duckdb.DuckDBPyConnection, build_dir: Path) -> Dict[str, Any]:
    """Extract thresholds and write build/thresholds.json. Returns the doc."""
    thresholds = extract_thresholds(con)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "financeaudit.finder.thresholds",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "llm_used": False,
        "thresholds": thresholds,
        "parameters": dict(RULE_PARAMETERS),
        "semantics": dict(SEMANTICS),
    }
    out_path = Path(build_dir) / "thresholds.json"
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


# ------------------------ accessors used by rules --------------------------

def tval(thresholds: Dict[str, Any], key: str) -> Any:
    """Value of a threshold from a loaded thresholds.json document."""
    return thresholds["thresholds"][key]["value"]


def tcite_source_id(thresholds: Dict[str, Any], key: str) -> Optional[str]:
    """source_id of the citation backing a threshold (None if config default)."""
    cit = thresholds["thresholds"][key].get("citation")
    return cit["source_id"] if cit else None


def param(thresholds: Dict[str, Any], key: str) -> Any:
    return thresholds["parameters"][key]


def lock_date_value(thresholds: Dict[str, Any]) -> Optional[date]:
    raw = tval(thresholds, "lock_date")
    if not raw:
        return None
    return date.fromisoformat(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract thresholds -> build/thresholds.json")
    ap.add_argument("--build", default="build")
    args = ap.parse_args()
    build_dir = Path(args.build)
    con = duckdb.connect(str(build_dir / "audit.duckdb"), read_only=True)
    doc = build_thresholds(con, build_dir)
    for key, entry in doc["thresholds"].items():
        cite = entry.get("citation") or {}
        print(f"{key}: {entry['value']} [{entry['provenance']}]"
              + (f" <- {cite.get('file')} ({cite.get('source_id')})" if cite else ""))


if __name__ == "__main__":
    main()
