#!/usr/bin/env bash
# Install or update Ortur Engraver (venv, deps, .env, optional git pull / systemd).
# Usage: ./scripts/install.sh
# Env: BRANCH=main  SKIP_GIT=1  SKIP_DIALOUT=1  INSTALL_SERVICE=1  LAN=1
set -euo pipefail

BRANCH="${BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$ROOT/server"

if [[ ! -f "$SERVER_DIR/run.py" ]]; then
  echo "error: expected server/run.py under $ROOT" >&2
  exit 1
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing '$1'." >&2
    exit 1
  }
}

need_cmd git
need_cmd python3

echo "==> Ortur Engraver install/update"
echo "    $ROOT"

if [[ "${SKIP_GIT:-0}" != "1" && -d "$ROOT/.git" ]]; then
  echo "==> git pull ($BRANCH)"
  git -C "$ROOT" fetch --prune origin
  git -C "$ROOT" checkout "$BRANCH"
  git -C "$ROOT" pull --ff-only origin "$BRANCH"
fi

if ! python3 -c "import venv" 2>/dev/null; then
  echo "error: python3-venv missing (sudo apt install python3-venv python3-pip)" >&2
  exit 1
fi

echo "==> python venv + deps"
[[ -d "$SERVER_DIR/.venv" ]] || python3 -m venv "$SERVER_DIR/.venv"
# shellcheck disable=SC1091
source "$SERVER_DIR/.venv/bin/activate"
python -m pip install -q --upgrade pip
pip install -q -r "$SERVER_DIR/requirements.txt"

# First-time .env only — never overwrite on updates
bash "$SCRIPT_DIR/setup-env.sh"

if [[ "${SKIP_DIALOUT:-0}" != "1" ]]; then
  if ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
    if command -v sudo >/dev/null 2>&1 && [[ -t 0 ]]; then
      echo "==> adding $USER to dialout (USB serial)"
      sudo usermod -aG dialout "$USER" || true
      echo "    log out/in (or reboot) for serial access to apply"
    else
      echo ""
      echo "Serial access (once):  sudo usermod -aG dialout $USER   then log out/in"
    fi
  fi
fi

if [[ "${INSTALL_SERVICE:-0}" == "1" ]]; then
  UNIT_SRC="$SCRIPT_DIR/ortur-engraver.service"
  UNIT_DST="/etc/systemd/system/ortur-engraver.service"
  echo "==> enabling boot service"
  TMP="$(mktemp)"
  sed -e "s|__USER__|$USER|g" -e "s|__ROOT__|$ROOT|g" "$UNIT_SRC" >"$TMP"
  sudo cp "$TMP" "$UNIT_DST"
  rm -f "$TMP"
  sudo systemctl daemon-reload
  sudo systemctl enable --now ortur-engraver.service
  echo "    status: sudo systemctl status ortur-engraver"
else
  echo ""
  echo "OK. Start with:  $ROOT/run.sh"
  echo "Boot service:    INSTALL_SERVICE=1 $0"
fi
