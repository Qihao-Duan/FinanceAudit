"""Central paths & constants. Read-only for module agents (see docs/CONTRACTS.md)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FA_DATA_DIR", REPO_ROOT / "data" / "practice"))
BUILD_DIR = Path(os.environ.get("FA_BUILD_DIR", REPO_ROOT / "build"))
DB_PATH = BUILD_DIR / "audit.duckdb"

EXTRACTOR_VERSION = "0.1.0"
GDPDU_ENCODING = "cp1252"
GDPDU_SEP = ";"
DATE_FMT = "%d.%m.%Y"

# LLM (optional — every call site needs a deterministic fallback; see CONTRACTS §0)
OPENAI_MODEL_REASONING = os.environ.get("FA_MODEL_REASONING", "gpt-5.2")
OPENAI_MODEL_FAST = os.environ.get("FA_MODEL_FAST", "gpt-5.2-mini")


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def ensure_build_dir() -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    return BUILD_DIR
