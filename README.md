# FinanceAudit — Cortea Track, {Tech: Europe} x Almedia Hackathon Berlin 2026

Auditor-facing agent that finds layered fraud in a GDPdU accounting dossier.
**Every claim links to the exact document, row/page and passage it rests on — no number without a source.**

Status: MVP under construction. See `docs/PLAN.md` (battle plan v5) and `docs/CONTRACTS.md` (module contracts).

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# place the dossier under data/practice/ (not in the repo)
python3 scripts/run_pipeline.py          # ingest → finder → claims → defense → verdict
./scripts/serve_ui.sh                    # evidence-card UI at http://127.0.0.1:8642
python3 -m evalx.run                     # regression metrics on the practice dossier
```

`OPENAI_API_KEY` enables the LLM adjudication/defense stages; without it the pipeline
runs fully deterministic and marks `llm_used: false`.

Data, keys, caches and model outputs are gitignored by default.
