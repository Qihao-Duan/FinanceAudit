"""Two-agent extensible structuring for dossier files no builtin adapter knows.

Design (PLAN §2.3 boundary rules apply):
- Agent 1 "Mapper"  : reads a deterministic structural PROFILE of the file and
  either selects an existing target table, designs a NEW declarative adapter
  spec, or declares the file unparseable. It never writes code and never
  transforms values — it emits a spec that deterministic code executes.
- Agent 2 "Verifier": independent context. Sees the spec, RAW sample lines and
  parsed-result statistics, and approves/rejects. One revision round; a second
  rejection leaves the file unparsed with an alert (wrongly structured audit
  evidence is worse than unstructured — robustness lesson S7).
- Both agents also run over KNOWN files in cheap batched confirmation mode:
  Agent 1 confirms the builtin adapter selection, Agent 2 sanity-checks the
  parse statistics. They can only FLAG (alerts); builtin deterministic parsing
  remains authoritative.
- Without OPENAI_API_KEY the stage degrades to today's behavior (unrecognized
  files stay unparsed) and raises the same alerts, marked llm_used=false.

Every outcome is surfaced in the manifest: `adapter_alerts` tells the user
both "unknown file detected" (报警) and "adapter auto-created" (已自主新增).
"""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from financeaudit.core import config
from .common import ManifestBuilder, SourceRegistry, sha256_file

BEGLEIT = "Begleitdokumente"
TABULAR_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx"}
EXT_PREFIX = "ext_"

# --------------------------------------------------------------------------
# deterministic structural profiling (code, no LLM)
# --------------------------------------------------------------------------


def _read_lines(path: Path, encoding: str, n: int = 400) -> List[str]:
    with open(path, encoding=encoding, errors="strict") as f:
        return [next(f).rstrip("\n\r") for _ in range(n)]


def _sniff_text(path: Path) -> Tuple[str, List[str], int]:
    """Return (encoding, all_lines, n_lines) with a strict->lenient chain."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            if enc != "utf-8-sig" and "�" in text:
                continue
            lines = text.splitlines()
            return enc, lines, len(lines)
        except (UnicodeDecodeError, UnicodeError):
            continue
    text = path.read_text(encoding="latin-1", errors="replace")
    lines = text.splitlines()
    return "latin-1", lines, len(lines)


def structural_profile(path: Path) -> Dict[str, Any]:
    """Everything the Mapper is allowed to know, computed deterministically."""
    suffix = path.suffix.lower()
    prof: Dict[str, Any] = {"file_name": path.name, "suffix": suffix,
                            "size_bytes": path.stat().st_size}
    if suffix == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 12:
                    break
                rows.append(["" if v is None else str(v) for v in row])
            sheets.append({"name": ws.title, "n_rows": ws.max_row,
                           "n_cols": ws.max_column, "head_rows": rows})
        wb.close()
        prof.update({"kind": "xlsx", "sheets": sheets})
        return prof

    enc, lines, n_lines = _sniff_text(path)
    delim_scores = {}
    for d in (";", ",", "\t", "|"):
        counts = [ln.count(d) for ln in lines[:50] if ln.strip()]
        if counts and min(counts) > 0 and len(set(counts)) <= 3:
            delim_scores[d] = sum(counts) / len(counts)
    # decimal-convention evidence: representative numeric tokens
    joined = "\n".join(lines[:80])
    german = len(re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}\b", joined)) \
        + len(re.findall(r"\d+,\d{2}\b", joined))
    dotted = len(re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}\b", joined)) \
        + len(re.findall(r"\d+\.\d{2}\b", joined))
    mid = max(1, n_lines // 2)
    prof.update({
        "kind": "text",
        "encoding_detected": enc,
        "n_lines": n_lines,
        "delimiter_candidates": delim_scores,
        "decimal_evidence": {"german_comma_tokens": german, "dot_decimal_tokens": dotted},
        "head_lines": lines[:8],
        "middle_lines": lines[mid:mid + 4],
        "tail_lines": lines[-3:] if n_lines > 12 else [],
    })
    return prof


# --------------------------------------------------------------------------
# agent prompts + schemas
# --------------------------------------------------------------------------

COLUMN_ROLES = ["amount", "date", "account", "counterparty_id", "counterparty_name",
                "doc_ref", "text", "identifier", "quantity", "currency", "other"]

MAPPER_SYSTEM = """You are the data-structuring MAPPER of an audit evidence pipeline.
You receive the structural profile of ONE file from an audit dossier that no built-in
parser recognizes, plus the list of existing target tables with their columns.

Decide exactly one of:
1. "use_existing" — the file is a format variant of data an existing table already
   models (same evidence population, compatible columns). Map its columns onto that
   table's columns.
2. "new_adapter" — coherent tabular data that fits no existing table. Design a new
   table: short snake_case English table name, one entry per source column with a
   snake_case name, a semantic role and a dtype.
3. "cannot_parse" — not coherent tabular data (free text, corrupted, no stable
   structure) or the structure cannot be determined with confidence.

HARD RULES
- You produce a DECLARATIVE parsing spec that deterministic code executes. You cannot
  write code and you cannot transform, invent or correct any value.
- Reference ONLY headers/positions that literally appear in the profile samples.
- Derive encoding/delimiter/decimal_convention/date_formats ONLY from evidence in the
  profile ("1.234,56" ⇒ decimal "comma"; "1,234.56" ⇒ decimal "dot"; the
  decimal_evidence token counts summarize this).
- Wrongly structured audit evidence is worse than unstructured evidence: if anything
  essential is ambiguous (e.g. the decimal convention is undecidable from the samples),
  choose "cannot_parse" and state exactly what is ambiguous.
- population_scope: ONE sentence stating what evidence population this file plausibly
  covers AND what it does not cover — downstream rules use it to gate absence claims.
- The reason field: 2-3 sentences a human auditor can audit your decision by."""

MAPPER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "reason", "target_table", "parse", "columns",
                 "population_scope", "confidence"],
    "properties": {
        "decision": {"type": "string", "enum": ["use_existing", "new_adapter", "cannot_parse"]},
        "reason": {"type": "string"},
        "target_table": {"type": "string",
                         "description": "existing table name, or snake_case new name; '' if cannot_parse"},
        "parse": {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "encoding", "delimiter", "decimal", "header_row", "sheet", "date_formats"],
            "properties": {
                "kind": {"type": "string", "enum": ["csv", "xlsx", "none"]},
                "encoding": {"type": "string", "enum": ["utf-8", "utf-8-sig", "cp1252", "latin-1"]},
                "delimiter": {"type": "string", "enum": [";", ",", "\t", "|", ""]},
                "decimal": {"type": "string", "enum": ["comma", "dot", "unknown"]},
                "header_row": {"type": "integer", "minimum": 0,
                               "description": "1-based row holding the headers; 0 = no header"},
                "sheet": {"type": ["string", "null"]},
                "date_formats": {"type": "array", "items": {"type": "string"}},
            },
        },
        "columns": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["source", "name", "role", "dtype"],
                "properties": {
                    "source": {"type": "string", "description": "header exactly as in the file, or 'col_<n>' when headerless"},
                    "name": {"type": "string", "description": "snake_case target column"},
                    "role": {"type": "string", "enum": COLUMN_ROLES},
                    "dtype": {"type": "string", "enum": ["text", "amount", "date", "int"]},
                },
            },
        },
        "population_scope": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

VERIFIER_SYSTEM = """You are the independent VERIFIER of an auto-generated parsing spec in
an audit evidence pipeline. Another model proposed the spec; deterministic code already
executed it. You see the file's structural profile (its RAW sample lines are ground
truth), the spec, parsed-result statistics, and parsed sample rows paired with the raw
lines they came from.

Approve ONLY if ALL of the following hold:
1. Column mapping is semantically plausible — amounts land in amount columns, dates
   parse as dates, identifiers are not mapped as amounts.
2. No silent data loss — the parsed row count is consistent with the raw data rows
   (minus header/section/footer lines).
3. The decimal convention is verifiably correct — cross-check at least one parsed
   amount character-by-character against its raw line; a ~100x or ~1000x discrepancy
   means the wrong convention was chosen.
4. Dates fall in a plausible audit-period range and are internally consistent.
5. The population_scope claim is justified by the actual content.

You verify against the RAW samples, never against the mapper's reasoning. Reject with
concrete issues (name the failed check and quote the evidence). When uncertain, reject:
wrongly structured audit evidence is worse than unstructured evidence."""

VERIFIER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "issues", "checks"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "reject"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "checks": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "passed", "detail"],
                "properties": {"name": {"type": "string"},
                               "passed": {"type": "boolean"},
                               "detail": {"type": "string"}},
            },
        },
    },
}

KNOWN_CONFIRM_SYSTEM = """You are the MAPPER of an audit evidence pipeline running in
confirmation mode over files the pipeline already parses with built-in adapters. For
each file you see its name, detected headers and the builtin target table. Confirm the
selection or flag it. You can only FLAG — the deterministic parse stays authoritative.
Flag when the headers plainly do not match the target table's purpose."""

KNOWN_CONFIRM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["files"],
    "properties": {"files": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["file", "ok", "note"],
        "properties": {"file": {"type": "string"}, "ok": {"type": "boolean"},
                       "note": {"type": "string"}}}}},
}

KNOWN_VERIFY_SYSTEM = """You are the VERIFIER of an audit evidence pipeline running in
confirmation mode. For each already-parsed file you see parse statistics (row counts,
null rates, amount sums, date ranges). Flag files whose statistics look structurally
wrong (e.g. 100% null in a key column, zero rows for a file with content, amount sums
that contradict the stated population). You can only FLAG — deterministic parsing stays
authoritative. Do not flag plausible business variation."""


# --------------------------------------------------------------------------
# generic declarative-spec executor (code, no LLM)
# --------------------------------------------------------------------------


def _parse_amount(raw: str, convention: str) -> Optional[Tuple[float, str]]:
    s = (raw or "").strip().replace(" ", "").replace("€", "").replace("EUR", "").strip()
    if not s:
        return None
    if convention == "comma":
        canon = s.replace(".", "").replace(",", ".")
    else:
        canon = s.replace(",", "")
    try:
        return float(Decimal(canon)), canon
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw: str, formats: List[str]) -> Optional[str]:
    from datetime import datetime
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in (formats or []) + ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"]:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _table_name(spec: dict) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", (spec.get("target_table") or "unknown").lower())[:40]
    return EXT_PREFIX + base.strip("_")


def execute_spec(data_dir: Path, rel: str, spec: dict,
                 registry: SourceRegistry) -> Tuple[str, List[dict], List[str]]:
    """Run a declarative adapter spec. Returns (table, rows, errors)."""
    path = data_dir / rel
    parse, cols = spec["parse"], spec["columns"]
    file_hash = sha256_file(path)
    errors: List[str] = []
    raw_rows: List[Tuple[int, List[str], str]] = []   # (1-based line/row no, cells, raw)

    if parse["kind"] == "csv":
        text = path.read_text(encoding=parse["encoding"], errors="strict")
        lines = text.splitlines()
        rdr = csv.reader(io.StringIO(text), delimiter=parse["delimiter"] or ";")
        for i, cells in enumerate(rdr, start=1):
            if i <= parse["header_row"] or not any(c.strip() for c in cells):
                continue
            raw_rows.append((i, cells, lines[i - 1] if i - 1 < len(lines) else ""))
        header = (next(csv.reader(io.StringIO(lines[parse["header_row"] - 1]),
                                  delimiter=parse["delimiter"] or ";"))
                  if parse["header_row"] >= 1 and lines else [])
    else:  # xlsx
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[parse["sheet"]] if parse.get("sheet") and parse["sheet"] in wb.sheetnames \
            else wb.active
        all_rows = [["" if v is None else str(v) for v in r]
                    for r in ws.iter_rows(values_only=True)]
        wb.close()
        header = all_rows[parse["header_row"] - 1] if parse["header_row"] >= 1 and all_rows else []
        for i, cells in enumerate(all_rows, start=1):
            if i <= parse["header_row"] or not any(str(c).strip() for c in cells):
                continue
            raw_rows.append((i, cells, ";".join(cells)))

    # source column -> index
    def _src_index(source: str) -> Optional[int]:
        m = re.fullmatch(r"col_(\d+)", source)
        if m:
            return int(m.group(1)) - 1
        for idx, h in enumerate(header):
            if str(h).strip() == source:
                return idx
        return None

    col_idx = {}
    for c in cols:
        idx = _src_index(c["source"])
        if idx is None:
            errors.append(f"spec column '{c['source']}' not found in header")
        else:
            col_idx[c["name"]] = (idx, c)
    if errors:
        return _table_name(spec), [], errors

    rows = []
    for rid, cells, raw_line in raw_rows:
        sid = registry.register(
            file=rel, file_hash=file_hash, kind="cell_row",
            locator=f"{rel}:row {rid}", content=raw_line or ";".join(map(str, cells)),
            display_locator=f"{rel}:row {rid}", row_no=rid)
        row: Dict[str, Any] = {"row_id": rid, "source_id": sid}
        for name, (idx, c) in col_idx.items():
            raw_v = str(cells[idx]).strip() if idx < len(cells) else ""
            if c["dtype"] == "amount":
                parsed = _parse_amount(raw_v, parse.get("decimal") or "comma")
                row[name] = parsed[0] if parsed else None
                row[name + "_raw"] = raw_v
                row[name + "_dec"] = parsed[1] if parsed else None
            elif c["dtype"] == "date":
                row[name] = _parse_date(raw_v, parse.get("date_formats") or [])
                row[name + "_raw"] = raw_v
            elif c["dtype"] == "int":
                row[name] = int(raw_v) if raw_v.lstrip("-").isdigit() else None
            else:
                row[name] = raw_v
        rows.append(row)
    return _table_name(spec), rows, errors


def _parse_stats(spec: dict, rows: List[dict], n_raw_data_rows: int) -> dict:
    stats: Dict[str, Any] = {"parsed_rows": len(rows), "raw_data_rows": n_raw_data_rows}
    for c in spec["columns"]:
        name = c["name"]
        vals = [r.get(name) for r in rows]
        nn = [v for v in vals if v not in (None, "")]
        cs: Dict[str, Any] = {"role": c["role"], "dtype": c["dtype"],
                              "null_rate": round(1 - len(nn) / len(vals), 3) if vals else None}
        if c["dtype"] == "amount" and nn:
            cs.update({"sum": round(sum(nn), 2), "min": min(nn), "max": max(nn)})
        if c["dtype"] == "date" and nn:
            cs.update({"min": min(nn), "max": max(nn)})
        stats[name] = cs
    return stats


def _sample_pairs(spec: dict, rows: List[dict], profile: dict, k: int = 4) -> List[dict]:
    out = []
    mids = rows[len(rows) // 2: len(rows) // 2 + k] if rows else []
    raw_mid = profile.get("middle_lines") or []
    for i, r in enumerate(mids):
        out.append({"parsed": {kk: vv for kk, vv in r.items()
                               if not kk.endswith("_dec") and kk != "source_id"},
                    "raw_line_nearby": raw_mid[i] if i < len(raw_mid) else None})
    return out


# --------------------------------------------------------------------------
# the two-agent loop
# --------------------------------------------------------------------------


def _llm(system: str, payload: dict, name: str, schema: dict) -> Optional[dict]:
    from financeaudit.llm.client import structured_call
    out = structured_call(config.OPENAI_MODEL_REASONING, system,
                          json.dumps(payload, ensure_ascii=False, default=str),
                          name, schema)
    if out is not None:
        out.pop("_usage", None)
    return out


def discover_unknown_files(data_dir: Path, known_files: set) -> List[str]:
    beg = data_dir / BEGLEIT
    if not beg.is_dir():
        return []
    out = []
    for p in sorted(beg.iterdir()):
        if (p.is_file() and not p.name.startswith(".")
                and p.name not in known_files
                and p.suffix.lower() in TABULAR_SUFFIXES):
            out.append(f"{BEGLEIT}/{p.name}")
    return out


def auto_structure(data_dir: Path, unknown_rels: List[str], existing_tables: Dict[str, list],
                   registry: SourceRegistry, manifest: ManifestBuilder):
    """Two-agent structuring for unrecognized tabular files.

    Returns (tables: {name: rows}, report: dict for manifest.json)."""
    report: Dict[str, Any] = {"llm_used": False, "alerts": [], "files": []}
    tables: Dict[str, list] = {}
    if not unknown_rels:
        return tables, report

    for rel in unknown_rels:
        report["alerts"].append(
            f"ALERT unknown file: {rel} matches no built-in adapter")

    if not config.llm_available():
        report["alerts"].append(
            "auto-adapter inactive (no OPENAI_API_KEY) — unknown files stay unparsed")
        return tables, report

    report["llm_used"] = True
    for rel in unknown_rels:
        path = data_dir / rel
        entry: Dict[str, Any] = {"file": rel}
        report["files"].append(entry)
        try:
            profile = structural_profile(path)
        except Exception as exc:
            entry.update(status="profile_error", error=str(exc)[:200])
            continue

        mapper_payload = {"profile": profile,
                          "existing_tables": existing_tables}
        spec = _llm(MAPPER_SYSTEM, mapper_payload, "adapter_spec", MAPPER_SCHEMA)
        entry["mapper"] = spec
        if spec is None or spec["decision"] == "cannot_parse":
            entry["status"] = "cannot_parse"
            report["alerts"].append(
                f"{rel}: mapper declared cannot_parse"
                + (f" — {spec['reason'][:160]}" if spec else " (API failure)"))
            continue

        verdict = None
        for attempt in (1, 2):
            try:
                table, rows, errors = execute_spec(data_dir, rel, spec, registry)
            except Exception as exc:
                errors, rows, table = [f"executor: {str(exc)[:200]}"], [], _table_name(spec)
            if errors or not rows:
                entry.update(status="execute_failed", errors=errors)
                break
            n_raw = (profile.get("n_lines", len(rows) + spec["parse"]["header_row"])
                     - spec["parse"]["header_row"]) if profile.get("kind") == "text" else len(rows)
            ver_payload = {
                "spec": spec,
                "profile_raw_samples": {k: profile.get(k) for k in
                                        ("head_lines", "middle_lines", "tail_lines", "sheets")},
                "parse_stats": _parse_stats(spec, rows, n_raw),
                "sample_pairs": _sample_pairs(spec, rows, profile),
                "attempt": attempt,
            }
            verdict = _llm(VERIFIER_SYSTEM, ver_payload, "adapter_verdict", VERIFIER_SCHEMA)
            entry[f"verifier_attempt_{attempt}"] = verdict
            if verdict and verdict["verdict"] == "approve":
                entry.update(status="approved", table=table, n_rows=len(rows))
                tables[table] = rows
                # replace the sidecars UNRECOGNIZED manifest row with the real one
                manifest.rows[:] = [m for m in manifest.rows if m["file"] != rel]
                manifest.add(
                    file=rel, file_hash=sha256_file(path),
                    size_bytes=path.stat().st_size,
                    expected_units=len(rows), parsed_units=len(rows),
                    parser_errors=[], parse_coverage="complete",
                    population_scope=spec["population_scope"]
                    + " [auto-adapter, LLM-mapped + independently verified]")
                report["alerts"].append(
                    f"AUTO-ADAPTER CREATED: {rel} -> table {table} "
                    f"({len(rows)} rows, mapper confidence {spec['confidence']}, "
                    f"verifier approved). Review the spec in build/auto_adapters.json.")
                break
            if attempt == 1 and verdict:
                revise = dict(mapper_payload)
                revise["verifier_issues"] = verdict["issues"]
                revise["previous_spec"] = spec
                spec2 = _llm(MAPPER_SYSTEM, revise, "adapter_spec", MAPPER_SCHEMA)
                if spec2 is None or spec2["decision"] == "cannot_parse":
                    entry["status"] = "rejected_then_cannot_parse"
                    break
                spec = spec2
                entry["mapper_revised"] = spec
        if entry.get("status") not in ("approved",):
            if entry.get("status") is None:
                entry["status"] = "rejected"
            report["alerts"].append(
                f"{rel}: auto-adapter NOT applied ({entry['status']}) — file stays "
                f"unparsed rather than risk a wrong structuring")
    return tables, report


def confirm_known(known_summary: List[dict], parse_stats: List[dict]) -> List[str]:
    """Batched two-agent confirmation over builtin-parsed files. Alerts only."""
    if not config.llm_available() or not known_summary:
        return []
    alerts: List[str] = []
    sel = _llm(KNOWN_CONFIRM_SYSTEM, {"files": known_summary},
               "known_confirmation", KNOWN_CONFIRM_SCHEMA)
    if sel:
        for f in sel["files"]:
            if not f["ok"]:
                alerts.append(f"mapper flag on builtin adapter: {f['file']} — {f['note'][:200]}")
    ver = _llm(KNOWN_VERIFY_SYSTEM, {"files": parse_stats},
               "known_verification", KNOWN_CONFIRM_SCHEMA)
    if ver:
        for f in ver["files"]:
            if not f["ok"]:
                alerts.append(f"verifier flag on parse stats: {f['file']} — {f['note'][:200]}")
    return alerts
