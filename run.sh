#!/usr/bin/env bash
# Ensure deps are installed, then start the Ortur Engraver UI.
# Usage: ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$ROOT/server"
VENV_PY="$SERVER/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "==> first run — installing…"
  SKIP_GIT="${SKIP_GIT:-1}" bash "$ROOT/scripts/install.sh"
fi

cd "$SERVER"
exec "$VENV_PY" run.py
