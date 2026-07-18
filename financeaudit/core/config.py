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

def _load_dotenv() -> None:
    """Minimal .env loader (repo root, gitignored). Never overrides real env.
    MUST run before any env-derived constant below is evaluated."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_dotenv()

# LLM (optional — every call site needs a deterministic fallback; see CONTRACTS §0)
# Model IDs verified against GET /v1/models on 2026-07-18.
OPENAI_MODEL_REASONING = os.environ.get("FA_MODEL_REASONING", "gpt-5.5")
OPENAI_MODEL_FAST = os.environ.get("FA_MODEL_FAST", "gpt-5.4-mini")


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def ensure_build_dir() -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    return BUILD_DIR
