# ui/ — Agent E (FastAPI backend + static frontend)

Run (from repo root, fully offline, no build step):

    /usr/bin/python3 -m uvicorn ui.server:app --port 8642
    # then open http://127.0.0.1:8642/

## Data sources and modes

- **build mode** (default when `build/findings.json` exists): reads
  `build/findings.json`, `build/manifest.json`, `build/audit.duckdb`
  (`source_registry`, `column_profiles`, contract tables for row rendering).
- **fixture mode** (auto-fallback when build artifacts are missing, or forced
  with `FA_UI_FIXTURES=1`): reads `ui/fixtures/*.fixture.json`; table/PDF/docx
  rendering goes straight against the raw files in `data/practice/`.
  Fixture citations point at real, verified rows of the practice dossier.

Env overrides: `FA_BUILD_DIR`, `FA_DATA_DIR`, `FA_UI_FIXTURES=1`.

## Endpoints

- `GET /api/findings` — compact list (id, title, scheme, disposition, amount,
  core-claim support counts)
- `GET /api/findings/{id}` — full finding (CONTRACTS §4 schema)
- `GET /api/manifest` — manifest + `summary` (parse-coverage %, dispositions,
  quarantine count, PBC queue)
- `GET /api/status` — mode + artifact presence
- `GET /api/source/{source_id}/render?quote=&precision=` —
  `{"kind":"table","html":…}` for table rows (surrounding rows, target
  highlighted) and docx paragraphs; `{"kind":"page","png_base64":…,
  "highlight":…, "locator_precision":…}` for PDF pages (PyMuPDF, quote
  highlight baked in; degrade quote_rect → word_rect → page_only).

## Frontend (`ui/web/`, hand-written ES modules, no npm)

Three panes: left findings list (disposition badges, coverage/quarantine
counters, PBC queue), center evidence card (assertion tree with verdict chips,
formula + UI re-check, defense log, three-state evidence status, denominators),
right source viewer (click any citation chip). Print CSS shows the evidence
card only.

Regenerate fixtures: `/usr/bin/python3 ui/fixtures/make_fixtures.py`.
