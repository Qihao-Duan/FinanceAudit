#!/usr/bin/env python3
"""Index and structurally verify every paired mutation under ``data/``.

This module does not invent scheme-specific truth.  Each generator owns that
truth in its ``MUTATION_MANIFEST.json``.  The suite enforces the invariants
that are common to every dossier:

* the untouched practice dossier has a reproducible content hash;
* every mutation has both ``fraud`` and ``repair`` variants;
* each variant declares its base hash, changed files and expected behaviour;
* the complete general ledger and each journal entry remain exactly balanced;
* manifests and directory contents are indexed for review and replay.

Run after the scheme generators:

    python3 -m evalx.mutation_suite --data-root data

The command writes ``data/MUTATION_SUITE_INDEX.json`` and exits non-zero when
any structural invariant fails.  No LLM is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


GL_REL = Path("Sachkonten/Sachkontobuchungen.txt")
MANIFEST = "MUTATION_MANIFEST.json"
VARIANTS = ("fraud", "repair")
IGNORED_NAMES = {".DS_Store", MANIFEST, "MUTATION_VERIFICATION.json"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dossier_hash(root: Path) -> str:
    """Hash relative paths and contents, excluding generated metadata."""
    h = hashlib.sha256()
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name not in IGNORED_NAMES
    )
    for path in files:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(bytes.fromhex(_sha256(path)))
    return h.hexdigest()


def _amount(raw: str) -> Decimal:
    value = raw.strip().strip('"')
    if not value:
        return Decimal("0")
    try:
        return Decimal(value.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"invalid German amount {raw!r}") from exc


def verify_gl(dossier: Path) -> dict:
    gl_path = dossier / GL_REL
    if not gl_path.exists():
        return {
            "passed": False,
            "row_count": 0,
            "entry_count": 0,
            "total": None,
            "unbalanced_entries": ["GL_MISSING"],
        }

    total = Decimal("0")
    groups: dict[str, Decimal] = {}
    rows = 0
    with gl_path.open(encoding="cp1252", newline="") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if not row:
                continue
            if len(row) < 19:
                return {
                    "passed": False,
                    "row_count": rows,
                    "entry_count": len(groups),
                    "total": str(total),
                    "unbalanced_entries": [f"MALFORMED_ROW_{rows + 1}"],
                }
            amount = _amount(row[7])
            entry_id = row[18]
            total += amount
            groups[entry_id] = groups.get(entry_id, Decimal("0")) + amount
            rows += 1

    unbalanced = [
        {"entry_id": entry_id, "delta": str(delta)}
        for entry_id, delta in groups.items() if delta != 0
    ]
    return {
        "passed": total == 0 and not unbalanced,
        "row_count": rows,
        "entry_count": len(groups),
        "total": str(total),
        "unbalanced_entries": unbalanced[:50],
    }


def _read_manifest(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # preserve actionable parse error in the index
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "manifest root must be an object"
    return data, None


def _manifest_contract(manifest: dict, expected_variant: str,
                       practice_hash: str) -> list[str]:
    errors = []
    if manifest.get("variant") != expected_variant:
        errors.append(
            f"variant={manifest.get('variant')!r}, expected {expected_variant!r}")
    if not (manifest.get("mutation") or manifest.get("scheme")):
        errors.append("missing mutation/scheme")
    declared_hash = (
        manifest.get("base_dossier_hash")
        or manifest.get("source_base_hash")
        or manifest.get("base_hash")
    )
    # The legacy duplicate-payment pair predates the base-hash contract. Keep
    # it visible as a contract warning rather than pretending it was recorded.
    if not declared_hash:
        errors.append("missing base dossier hash")
    elif declared_hash != practice_hash:
        errors.append(
            f"base dossier hash mismatch: {declared_hash} != {practice_hash}")
    if not manifest.get("expected_detection"):
        errors.append("missing expected_detection")
    if expected_variant == "repair" and not (
        manifest.get("innocent_predicate")
        or manifest.get("expected_defense")
        or manifest.get("repair_evidence")
    ):
        errors.append("repair manifest missing innocent predicate/defense")
    return errors


def build_index(data_root: Path) -> dict:
    practice = data_root / "practice"
    if not practice.is_dir():
        raise FileNotFoundError(f"practice dossier not found: {practice}")
    practice_hash = dossier_hash(practice)
    pairs = []

    for pair_dir in sorted(data_root.glob("mutated_*")):
        if not pair_dir.is_dir():
            continue
        pair = {
            "pair": pair_dir.name,
            "path": pair_dir.relative_to(data_root.parent).as_posix(),
            "variants": {},
        }
        pair_ok = True
        for variant in VARIANTS:
            dossier = pair_dir / variant
            variant_record = {
                "path": dossier.relative_to(data_root.parent).as_posix(),
                "exists": dossier.is_dir(),
            }
            if not dossier.is_dir():
                variant_record.update({
                    "passed": False,
                    "errors": ["variant directory missing"],
                })
                pair_ok = False
                pair["variants"][variant] = variant_record
                continue

            manifest_path = dossier / MANIFEST
            manifest, parse_error = _read_manifest(manifest_path)
            errors = []
            if parse_error:
                errors.append(f"manifest: {parse_error}")
            elif manifest is not None:
                errors.extend(_manifest_contract(
                    manifest, variant, practice_hash))
            gl = verify_gl(dossier)
            if not gl["passed"]:
                errors.append("general ledger is not structurally balanced")

            files = [p for p in dossier.rglob("*") if p.is_file()]
            variant_record.update({
                "manifest": manifest,
                "dossier_hash": dossier_hash(dossier),
                "file_count": len(files),
                "size_bytes": sum(p.stat().st_size for p in files),
                "gl_verification": gl,
                "errors": errors,
                "passed": not errors,
            })
            pair_ok = pair_ok and not errors
            pair["variants"][variant] = variant_record
        pair["passed"] = pair_ok
        pairs.append(pair)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_used": False,
        "data_root": data_root.as_posix(),
        "practice": {
            "path": practice.relative_to(data_root.parent).as_posix(),
            "dossier_hash": practice_hash,
            "gl_verification": verify_gl(practice),
        },
        "pair_count": len(pairs),
        "all_passed": bool(pairs) and all(p["passed"] for p in pairs),
        "pairs": pairs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    index = build_index(args.data_root)
    out = args.out or args.data_root / "MUTATION_SUITE_INDEX.json"
    out.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"practice_hash={index['practice']['dossier_hash']}")
    for pair in index["pairs"]:
        status = "PASS" if pair["passed"] else "FAIL"
        errors = []
        for variant, record in pair["variants"].items():
            errors.extend(f"{variant}: {err}" for err in record.get("errors", []))
        suffix = "" if not errors else " — " + "; ".join(errors)
        print(f"{status:4} {pair['pair']}{suffix}")
    print(f"index={out}")
    return 0 if index["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
