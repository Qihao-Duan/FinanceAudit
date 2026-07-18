"""FinanceAudit UI server (Agent E).

FastAPI backend serving findings + evidence rendering for the three-pane
audit UI in ui/web/.

Data sources (per CONTRACTS.md §5):
  build/findings.json  build/manifest.json  build/audit.duckdb
Fallback (FA_UI_FIXTURES=1 or build artifacts missing):
  ui/fixtures/*.fixture.json + raw files in data/practice/

Run:  uvicorn ui.server:app --port 8642
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = Path(os.environ.get("FA_BUILD_DIR", ROOT / "build"))
DATA_DIR = Path(os.environ.get("FA_DATA_DIR", ROOT / "data" / "practice"))
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FORCE_FIXTURES = os.environ.get("FA_UI_FIXTURES", "") == "1"

CONTRACT_TABLES = [
    "gl", "vendors", "customers", "vendor_tx", "customer_tx", "assets",
    "asset_tx", "goods_receipts", "goods_issues", "sales_invoices",
    "purchase_invoices_2026", "subsequent_payments", "approval_log",
    "masterdata_changes", "permissions", "credit_limits", "shareholders",
    "trial_balance", "op_debitors_accounts", "op_debitors_items",
    "op_creditors_accounts", "reconciliation",
]

app = FastAPI(title="FinanceAudit UI", docs_url=None, redoc_url=None)

# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------

_cache: Dict[str, Any] = {}


def _load_json(path: Path) -> Any:
    key = str(path)
    mtime = path.stat().st_mtime
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    _cache[key] = (mtime, data)
    return data


def build_artifacts_present() -> bool:
    return (BUILD_DIR / "findings.json").exists()


def mode() -> str:
    if FORCE_FIXTURES or not build_artifacts_present():
        return "fixtures"
    return "build"


def get_findings() -> List[dict]:
    if mode() == "build":
        data = _load_json(BUILD_DIR / "findings.json")
        # tolerate both bare list and {"findings": [...]}
        return data["findings"] if isinstance(data, dict) and "findings" in data else data
    return _load_json(FIXTURE_DIR / "findings.fixture.json")


def get_manifest() -> dict:
    if mode() == "build" and (BUILD_DIR / "manifest.json").exists():
        return _load_json(BUILD_DIR / "manifest.json")
    return _load_json(FIXTURE_DIR / "manifest.fixture.json")


def _duck():
    con = _cache.get("_duck_con")
    if con is None:
        import duckdb
        con = duckdb.connect(str(BUILD_DIR / "audit.duckdb"), read_only=True)
        _cache["_duck_con"] = con
    return con


def lookup_source(source_id: str) -> Optional[dict]:
    """Return normalized source record: file, kind, row_no, page_index, para_no,
    page_label, display_locator, plus fixture file spec if any."""
    if mode() == "fixtures":
        reg = _load_json(FIXTURE_DIR / "source_registry.fixture.json")
        rec = reg["sources"].get(source_id)
        if rec:
            rec = dict(rec)
            rec["file_spec"] = reg["files"].get(rec["file"])
        return rec
    if not (BUILD_DIR / "audit.duckdb").exists():
        return None
    con = _duck()
    rows = con.execute(
        "SELECT source_id, file, kind, row_no, page_index, page_label, para_no, display_locator "
        "FROM source_registry WHERE source_id = ?", [source_id]).fetchall()
    if not rows:
        return None
    r = rows[0]
    return {"source_id": r[0], "file": r[1], "kind": r[2], "row_no": r[3],
            "page_index": r[4], "page_label": r[5], "para_no": r[6],
            "display_locator": r[7], "file_spec": None}


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if s is not None else "")


def _html_table(header: List[str], rows: List[List[Any]], hi_index: int,
                first_line_no: Optional[int] = None) -> str:
    out = ['<table class="src-table"><thead><tr>']
    if first_line_no is not None:
        out.append('<th class="ln">Zeile</th>')
    out += [f"<th>{_esc(h)}</th>" for h in header]
    out.append("</tr></thead><tbody>")
    for i, row in enumerate(rows):
        cls = ' class="hi"' if i == hi_index else ""
        out.append(f"<tr{cls}>")
        if first_line_no is not None:
            out.append(f'<td class="ln">{first_line_no + i + 1}</td>')
        out += [f"<td>{_esc(c)}</td>" for c in row]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render_delimited(path: Path, spec: Optional[dict], row_no: int,
                     context: int = 4) -> dict:
    enc = (spec or {}).get("encoding", "cp1252")
    delim = (spec or {}).get("delimiter", ";")
    quote = (spec or {}).get("quotechar", '"')
    has_header = (spec or {}).get("has_header", True)
    columns = (spec or {}).get("columns")
    with open(path, encoding=enc, errors="replace", newline="") as f:
        lines = f.read().splitlines()
    if row_no < 0 or row_no >= len(lines):
        raise HTTPException(404, f"row {row_no} out of range for {path.name}")
    lo = max(1 if has_header else 0, row_no - context)
    hi = min(len(lines), row_no + context + 1)
    window = lines[lo:hi]
    parsed = list(csv.reader(io.StringIO("\n".join(window)),
                             delimiter=delim, quotechar=quote))
    if has_header:
        header = next(csv.reader(io.StringIO(lines[0]), delimiter=delim, quotechar=quote))
    elif columns:
        header = columns
    else:
        header = [f"Spalte {i+1}" for i in range(len(parsed[0]) if parsed else 0)]
    width = len(header)
    parsed = [r[:width] + [""] * (width - len(r)) for r in parsed]
    return {"kind": "table",
            "html": _html_table(header, parsed, row_no - lo, first_line_no=lo)}


def render_xlsx(path: Path, spec: Optional[dict], row_no: int,
                context: int = 3) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = (spec or {}).get("sheet")
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    header_row = (spec or {}).get("header_row", 1)
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = ["" if v is None else str(v) for v in all_rows[header_row - 1]] \
        if 0 < header_row <= len(all_rows) else []
    lo = max(header_row, row_no - 1 - context)   # 0-based index into all_rows
    hi = min(len(all_rows), row_no + context)
    body = [["" if v is None else v for v in r] for r in all_rows[lo:hi]]
    return {"kind": "table",
            "html": _html_table(header, body, row_no - 1 - lo, first_line_no=lo)}


def render_docx(path: Path, para_no: int, context: int = 2) -> dict:
    import docx
    doc = docx.Document(str(path))
    paras = [p.text for p in doc.paragraphs]
    if para_no >= len(paras):
        raise HTTPException(404, f"paragraph {para_no} out of range")
    lo, hi = max(0, para_no - context), min(len(paras), para_no + context + 1)
    parts = ['<div class="src-doc">']
    for i in range(lo, hi):
        cls = "para hi" if i == para_no else "para"
        parts.append(f'<p class="{cls}"><span class="pno">¶{i+1}</span>{_esc(paras[i])}</p>')
    parts.append("</div>")
    return {"kind": "table", "html": "".join(parts)}


def render_pdf(path: Path, page_index: int, quote: Optional[str],
               precision_hint: Optional[str]) -> dict:
    import fitz
    doc = fitz.open(str(path))
    if page_index >= len(doc):
        raise HTTPException(404, f"page {page_index} out of range")
    page = doc[page_index]
    precision = "page_only"
    rects = []
    if quote:
        rects = page.search_for(quote)
        if rects:
            precision = "quote_rect"
        else:  # degrade: successively shorter word prefixes
            words = quote.split()
            for n in (6, 4, 2, 1):
                if len(words) < n:
                    continue
                rects = page.search_for(" ".join(words[:n]))
                if rects:
                    precision = "word_rect"
                    break
    if precision_hint == "page_only":
        rects, precision = [], "page_only"
    for r in rects:
        page.draw_rect(r + (-2, -2, 2, 2), color=(0.75, 0.29, 0.06),
                       fill=(0.98, 0.78, 0.35), fill_opacity=0.30, width=1.4)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    png = base64.b64encode(pix.tobytes("png")).decode("ascii")
    doc.close()
    return {"kind": "page", "png_base64": png, "highlight": bool(rects),
            "locator_precision": precision, "page_index": page_index,
            "page_label": str(page_index + 1)}


def render_from_duckdb(rec: dict) -> Optional[dict]:
    """Build mode: try to show the typed table row from audit.duckdb."""
    try:
        con = _duck()
        table = None
        prof = con.execute(
            "SELECT table_name FROM column_profiles WHERE file = ? LIMIT 1",
            [rec["file"]]).fetchall()
        if prof:
            table = prof[0][0]
        candidates = [table] if table else CONTRACT_TABLES
        for t in candidates:
            try:
                hit = con.execute(
                    f'SELECT row_id FROM "{t}" WHERE source_id = ?',
                    [rec["source_id"]]).fetchall()
            except Exception:
                continue
            if not hit:
                continue
            row_id = hit[0][0]
            cur = con.execute(
                f'SELECT * FROM "{t}" WHERE row_id BETWEEN ? AND ? ORDER BY row_id',
                [row_id - 4, row_id + 4])
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            hi = next((i for i, r in enumerate(rows)
                       if r[cols.index("row_id")] == row_id), 0)
            return {"kind": "table",
                    "html": _html_table(cols, [list(r) for r in rows], hi)}
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

def _amount_of(f: dict) -> Optional[float]:
    if isinstance(f.get("amount_eur"), (int, float)):
        return f["amount_eur"]
    for c in f.get("claims", []):
        v = c.get("value")
        if c.get("role") == "core" and v and v.get("kind") == "amount":
            return v.get("value")
    return None


@app.get("/api/findings")
def api_findings():
    out = []
    for f in get_findings():
        claims = f.get("claims", [])
        core = [c for c in claims if c.get("role") == "core"]
        out.append({
            "finding_id": f.get("finding_id"),
            "title": f.get("title") or f.get("description", "")[:90],
            "scheme": f.get("scheme"),
            "anomaly_type": f.get("anomaly_type"),
            "reporting_status": f.get("reporting_status"),
            "disposition": f.get("disposition"),
            "entity_label": f.get("entity_label") or f.get("entity_key"),
            "amount_eur": _amount_of(f),
            "n_claims": len(claims),
            "core_supported": sum(1 for c in core if c.get("verdict") == "supported"),
            "core_total": len(core),
            "n_missing_evidence": len(f.get("missing_evidence", [])),
            "n_pbc": len(f.get("pbc_requests", [])),
        })
    return out


@app.get("/api/findings/{finding_id}")
def api_finding(finding_id: str):
    for f in get_findings():
        if f.get("finding_id") == finding_id:
            return f
    raise HTTPException(404, f"finding {finding_id} not found")


@app.get("/api/manifest")
def api_manifest():
    man = dict(get_manifest())
    findings = get_findings()
    disp: Dict[str, int] = {}
    for f in findings:
        d = f.get("disposition", "unknown")
        disp[d] = disp.get(d, 0) + 1
    pbc = [{"finding_id": f.get("finding_id"), "title": f.get("title"),
            "disposition": f.get("disposition"),
            "requests": f.get("pbc_requests", [])}
           for f in findings if f.get("pbc_requests")]
    files = man.get("files", [])
    exp = sum(x.get("expected_units", 0) for x in files)
    got = sum(x.get("parsed_units", 0) for x in files)
    man["summary"] = {
        "mode": mode(),
        "parse_coverage_pct": round(100.0 * got / exp, 1) if exp else None,
        "n_files": len(files),
        "n_files_complete": sum(1 for x in files
                                if x.get("parse_coverage") == "complete"),
        "dispositions": disp,
        "quarantine_count": disp.get("quarantine", 0),
        "pbc_queue": pbc,
    }
    return man


@app.get("/api/status")
def api_status():
    return {
        "ok": True,
        "mode": mode(),
        "build_dir": str(BUILD_DIR),
        "data_dir": str(DATA_DIR),
        "data_dir_present": DATA_DIR.exists(),
        "artifacts": {
            "findings_json": (BUILD_DIR / "findings.json").exists(),
            "manifest_json": (BUILD_DIR / "manifest.json").exists(),
            "audit_duckdb": (BUILD_DIR / "audit.duckdb").exists(),
        },
        "n_findings": len(get_findings()),
        "llm_used": False,
    }


@app.get("/api/source/{source_id}/render")
def api_render(source_id: str,
               quote: Optional[str] = Query(None),
               precision: Optional[str] = Query(None)):
    rec = lookup_source(source_id)
    if rec is None:
        raise HTTPException(404, f"source_id {source_id} not found in registry")
    file_rel = rec["file"]
    path = DATA_DIR / file_rel
    caption = rec.get("display_locator") or file_rel
    kind = rec.get("kind")
    suffix = path.suffix.lower()
    result: Optional[dict] = None

    if kind in ("cell_row", "table_row", "row") or (
            kind is None and suffix in (".txt", ".csv", ".xlsx")):
        if mode() == "build":
            result = render_from_duckdb(rec)
        if result is None:
            if not path.exists():
                raise HTTPException(404, f"source file missing: {file_rel}")
            if suffix == ".xlsx":
                result = render_xlsx(path, rec.get("file_spec"), int(rec["row_no"]))
            else:
                result = render_delimited(path, rec.get("file_spec"), int(rec["row_no"]))
    elif suffix == ".docx" or (kind == "para" and suffix != ".pdf"):
        if not path.exists():
            raise HTTPException(404, f"source file missing: {file_rel}")
        result = render_docx(path, int(rec.get("para_no") or 0))
    elif suffix == ".pdf" or kind in ("page", "para"):
        if not path.exists():
            raise HTTPException(404, f"source file missing: {file_rel}")
        result = render_pdf(path, int(rec.get("page_index") or 0), quote, precision)
    else:
        raise HTTPException(422, f"cannot render kind={kind} file={file_rel}")

    result["source_id"] = source_id
    result["file"] = file_rel
    result["caption"] = caption
    return JSONResponse(result)


app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent / "web"),
                           html=True), name="web")
