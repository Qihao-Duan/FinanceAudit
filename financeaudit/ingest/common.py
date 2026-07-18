"""Shared helpers for the ingest layer: hashing, German-format parsing, source registry.

Every citable unit (table row, docx paragraph/table row, pdf page/block) receives a
versioned source_id = sha1(file_hash|extractor_version|locator|content_hash)[:16].
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
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
    """Read a ;-separated cp1252 file. Returns list of (physical_line_no_1based, fields, raw_line)."""
    out = []
    with open(path, encoding=ENCODING, newline="") as fh:
        raw_lines = fh.read().splitlines()
    for i, raw in enumerate(raw_lines, start=1):
        fields = next(csv.reader([raw], delimiter=SEP, quotechar='"'))
        out.append((i, fields, raw))
    return out


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
    ):
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
                "extractor_version": EXTRACTOR_VERSION,
            }
        )
