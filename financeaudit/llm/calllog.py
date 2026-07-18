"""Agent-conversation debug log (user request 2026-07-18).

Every LLM call (request + response/error + latency) and every enrichment
filter decision is appended as one JSONL line to

    <data_dir>/_agent_logs/llm_calls_<YYYYMMDD>.jsonl      (default)
    $FA_AGENT_LOG_DIR/llm_calls_<YYYYMMDD>.jsonl            (override)

so each dossier's dialogue history lives next to the dossier it belongs to,
timestamped, for prompt/behaviour optimization. The directory is
underscore-prefixed and outside Begleitdokumente, so ingest never scans it.
Set FA_AGENT_LOG=0 to disable. data/ is gitignored — logs never reach the repo.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _enabled() -> bool:
    return os.environ.get("FA_AGENT_LOG", "1") != "0"


def _log_dir() -> Path:
    override = os.environ.get("FA_AGENT_LOG_DIR")
    if override:
        return Path(override)
    from financeaudit.core.config import DATA_DIR
    return Path(DATA_DIR) / "_agent_logs"


def log_event(kind: str, payload: dict) -> None:
    """Append one event. Never raises — logging must not break the pipeline."""
    if not _enabled():
        return
    try:
        now = datetime.now(timezone.utc)
        rec = {
            "ts": now.isoformat(timespec="milliseconds"),
            "kind": kind,                       # llm_request | llm_response | llm_error | filter_decision
            "stage": os.environ.get("FA_STAGE", ""),
            "pid": os.getpid(),
            **payload,
        }
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"llm_calls_{now.strftime('%Y%m%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
