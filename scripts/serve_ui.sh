#!/usr/bin/env bash
# Start the FinanceAudit UI regardless of current working directory.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8642}"
exec python3 -m uvicorn ui.server:app --app-dir "$REPO" --host 127.0.0.1 --port "$PORT"
