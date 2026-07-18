"""Shared helpers for the ingest layer: hashing, German-format parsing, source registry.

Every citable unit (table row, docx paragraph/table row, pdf page/block) receives a
versioned source_id = sha1(file_hash|extractor_version|locator|content_hash)[:16].
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime  # noqa: F401 (date re-exported for callers)
from decimal import Decimal
from pathlib import Path
from typing import Optional

EXTRACTOR_VERSION = "0.1.0"
ENCODING = "cp1252"
SEP = ";"

_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def make_source_id(file_hash: str, locator: str, content_hash: str) -> str:
    return hashlib.sha1(
        f"{file_hash}|{EXTRACTOR_VERSION}|{locator}|{content_hash}".encode("utf-8")
    ).hexdigest()[:16]


def parse_german_date(s: Optional[str]):
    """DD.MM.YYYY -> datetime.date, else None."""
    if s is None:
        return None
    s = str(s).strip().strip('"')
    if not s or not _DATE_RE.match(s):
        return None
    return datetime.strptime(s, "%d.%m.%Y").date()


def parse_german_amount(s: Optional[str]):
    """German decimal-comma (optional thousands dots) -> (float, raw, canonical_dec_str).

    canonical_dec_str is a plain dot-decimal string safe for Decimal() downstream.
    Returns (None, None, None) for empty input; raises ValueError on garbage
    (callers surface that as a parser error -> A-class typing test).
    """
    if s is None:
        return None, None, None
    raw = str(s).strip()
    if not raw:
        return None, None, None
    canon = raw.replace(".", "").replace(",", ".")
    val = float(Decimal(canon))  # raises on garbage
    return val, raw, canon


def excel_amount(v):
    """openpyxl numeric cell -> (float, raw_str, canonical_dec_str)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None, None
    if isinstance(v, str):
        return parse_german_amount(v)
    canon = repr(float(v)) if isinstance(v, float) else str(v)
    return float(v), str(v), canon


def dec(canon: Optional[str]) -> Decimal:
    """Canonical dot-decimal string -> Decimal (0 for None)."""
    return Decimal(canon) if canon else Decimal(0)


def read_delimited(path: Path):
    """Read a ;-separated cp1252 file. Returns list of (physical_line_no_1based, fields, raw_line).

    ROBUSTNESS (evalx/robustness_test S7): the GDPdU export convention is cp1252, but a
    finals dossier could ship a UTF-8 sidecar. We try cp1252 first (lossless for the
    practice set) and fall back to utf-8 only when cp1252 raises a decode error, so a
    UTF-8 file never hard-crashes the ingest. The chosen encoding is returned via the
    module-level last-read marker for the caller's manifest note.
    """
    global LAST_READ_ENCODING
    try:
        with open(path, encoding=ENCODING, newline="") as fh:
            text = fh.read()
        LAST_READ_ENCODING = ENCODING
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        LAST_READ_ENCODING = "utf-8"
    out = []
    for i, raw in enumerate(text.splitlines(), start=1):
        fields = next(csv.reader([raw], delimiter=SEP, quotechar='"'))
        out.append((i, fields, raw))
    return out


# module-level marker set by the most recent read_delimited() call (see docstring)
LAST_READ_ENCODING = ENCODING

# --- decimal-convention detection (ROBUSTNESS S7) --------------------------
# GDPdU/German convention: '.' thousands, ',' decimal ("1.234,56" | "1234,56" | "1.234").
# Dot-decimal convention (e.g. a UTF-8 finals export): '.' decimal, optional ',' thousands
# ("1234.56" | "1,234.56"). Integer-only tokens are convention-neutral.
_DE_AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+(?:,\d+)?$|^-?\d+,\d+$")
_DOT_DECIMAL_RE = re.compile(r"^-?\d+\.\d{1,2}$")
_US_GROUPED_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+\.\d{1,2}$")


def detect_decimal_convention(raw_values) -> str:
    """Infer 'de' (German comma-decimal, the default) or 'dot' (dot-decimal) for one column.

    Conservative: returns 'dot' ONLY when the column shows dot-decimal evidence and NO
    German-specific evidence. Any comma-decimal or dot-thousands token forces 'de'. This
    keeps every practice-set column on 'de' (byte-identical output) while a purely
    dot-decimal finals column parses correctly instead of being silently inflated ~100x.
    """
    dot = de = 0
    for v in raw_values:
        s = str(v).strip()
        if not s:
            continue
        if _DE_AMOUNT_RE.match(s):
            de += 1
        elif _DOT_DECIMAL_RE.match(s) or _US_GROUPED_RE.match(s):
            dot += 1
    return "dot" if de == 0 and dot > 0 else "de"


def parse_amount_conv(s: Optional[str], conv: str):
    """Convention-aware amount parse. conv=='dot' -> dot decimal (optional ',' thousands);
    anything else delegates to parse_german_amount (unchanged default path)."""
    if conv != "dot":
        return parse_german_amount(s)
    if s is None:
        return None, None, None
    raw = str(s).strip()
    if not raw:
        return None, None, None
    canon = raw.replace(",", "")  # strip US thousands separators; '.' stays decimal
    val = float(Decimal(canon))   # raises on garbage -> caller surfaces as parser error
    return val, raw, canon


class SourceRegistry:
    """Accumulates source_registry rows; hands out source_ids."""

    def __init__(self):
        self.rows = []  # dicts matching the source_registry table schema
        self._seen = set()

    def register(
        self,
        *,
        file: str,
        file_hash: str,
        kind: str,  # cell_row | para | table | page
        locator: str,
        content: str,
        display_locator: str,
        row_no: Optional[int] = None,
        page_index: Optional[int] = None,
        page_label: Optional[str] = None,
        para_no: Optional[int] = None,
    ) -> str:
        content_hash = sha1_text(content)
        sid = make_source_id(file_hash, locator, content_hash)
        if sid in self._seen:  # deterministic collision (same locator re-registered)
            return sid
        self._seen.add(sid)
        self.rows.append(
            {
                "source_id": sid,
                "file": file,
                "kind": kind,
                "row_no": row_no,
                "page_index": page_index,
                "page_label": page_label,
                "para_no": para_no,
                "content_hash": content_hash,
                "display_locator": display_locator,
            }
        )
        return sid


class ManifestBuilder:
    def __init__(self):
        self.rows = []

    def add(
        self,
        *,
        file: str,
        file_hash: Optional[str],
        size_bytes: Optional[int],
        expected_units: Optional[int],
        parsed_units: int,
        parser_errors: list,
        population_scope: str,
        parse_coverage: Optional[str] = None,
        provided: bool = True,
    ):
        # `provided` distinguishes a file that is ABSENT from the dossier (provided=False,
        # a legitimate coverage gap that degrades) from a file that was present but our
        # parser could not read (a real bug). A-class parser-self-consistency tests skip
        # not-provided files; the core GL is guarded separately (proptests A1b).
        if parse_coverage is None:
            if parser_errors:
                parse_coverage = "partial" if parsed_units else "failed"
            elif expected_units is not None and parsed_units != expected_units:
                parse_coverage = "partial"
            else:
                parse_coverage = "complete"
        self.rows.append(
            {
                "file": file,
                "file_hash": file_hash,
                "size_bytes": size_bytes,
                "expected_units": expected_units,
                "parsed_units": parsed_units,
                "parser_errors": parser_errors,
                "parse_coverage": parse_coverage,
                "population_scope": population_scope,
                "provided": provided,
                "extractor_version": EXTRACTOR_VERSION,
            }
        )
