#!/usr/bin/env bash
# Install or update Ortur Engraver (venv, deps, .env, optional git pull / systemd).
# Usage: ./scripts/install.sh
# Env: BRANCH=main  SKIP_GIT=1  SKIP_DIALOUT=1  INSTALL_SERVICE=auto|1|0  LAN=1
#
# Run as your normal user (pi), NOT with sudo. The script prompts for sudo only
# when installing the boot service into /etc/systemd/system.
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

systemd_host() {
  [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1
}

# Never run the whole install as root — that makes the tree root-owned and breaks later updates.
if [[ "$(id -u)" -eq 0 ]]; then
  echo "error: do not run install.sh with sudo / as root." >&2
  echo "       run:  ./scripts/install.sh" >&2
  echo "       (sudo is only prompted for the boot service step)" >&2
  exit 1
fi

if [[ ! -w "$ROOT" ]] || [[ -d "$ROOT/.git" && ! -w "$ROOT/.git" ]]; then
  echo "error: $ROOT is not writable by $USER." >&2
  echo "       Often caused by an earlier 'sudo' install. Fix ownership, then re-run:" >&2
  echo "         sudo chown -R \"$USER:$USER\" \"$ROOT\"" >&2
  echo "         $ROOT/scripts/install.sh" >&2
  exit 1
fi

# Pi / systemd hosts: enable boot service by default. Set INSTALL_SERVICE=0 to skip.
INSTALL_SERVICE="${INSTALL_SERVICE:-auto}"
if [[ "$INSTALL_SERVICE" == "auto" ]]; then
  if systemd_host; then
    INSTALL_SERVICE=1
  else
    INSTALL_SERVICE=0
  fi
fi

echo "==> Ortur Engraver install/update"
echo "    $ROOT  (user: $USER)"

if [[ "${SKIP_GIT:-0}" != "1" && -d "$ROOT/.git" ]]; then
  echo "==> git sync ($BRANCH)"
  if ! git -C "$ROOT" fetch --prune origin; then
    echo "error: git fetch failed (network / permissions)." >&2
    exit 1
  fi
  git -C "$ROOT" checkout "$BRANCH"
  # Hard sync so dirty trees (common on Pi) still update; .env stays (gitignored).
  if ! git -C "$ROOT" reset --hard "origin/$BRANCH"; then
    echo "error: git reset failed — check file ownership:" >&2
    echo "         sudo chown -R \"$USER:$USER\" \"$ROOT\"" >&2
    exit 1
  fi
  git -C "$ROOT" clean -fd || true
fi

if ! python3 -c "import venv" 2>/dev/null; then
  echo "error: python3-venv missing (sudo apt install python3-venv python3-pip)" >&2
  exit 1
fi

echo "==> python venv + deps"
if [[ -d "$SERVER_DIR/.venv" && ! -w "$SERVER_DIR/.venv" ]]; then
  echo "error: server/.venv is not writable by $USER (likely created with sudo)." >&2
  echo "       fix:  sudo chown -R \"$USER:$USER\" \"$SERVER_DIR/.venv\"" >&2
  echo "       or:   rm -rf \"$SERVER_DIR/.venv\" && re-run install" >&2
  exit 1
fi
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
      echo "==> adding $USER to dialout (USB serial) — sudo password may be required"
      sudo usermod -aG dialout "$USER" || true
      echo "    log out/in (or reboot) for serial access to apply"
    else
      echo ""
      echo "Serial access (once):  sudo usermod -aG dialout $USER   then log out/in"
    fi
  fi
fi

if [[ "$INSTALL_SERVICE" == "1" ]]; then
  if ! systemd_host; then
    echo "warning: systemd not available — skip boot service" >&2
  elif ! command -v sudo >/dev/null 2>&1; then
    echo "warning: sudo missing — skip boot service" >&2
  else
    UNIT_SRC="$SCRIPT_DIR/ortur-engraver.service"
    UNIT_DST="/etc/systemd/system/ortur-engraver.service"
    echo "==> enabling boot service (ortur-engraver) — sudo password may be required"
    TMP="$(mktemp)"
    sed -e "s|__USER__|$USER|g" -e "s|__ROOT__|$ROOT|g" "$UNIT_SRC" >"$TMP"
    if ! sudo tee "$UNIT_DST" >/dev/null <"$TMP"; then
      rm -f "$TMP"
      echo "error: could not write $UNIT_DST (sudo failed or was cancelled)." >&2
      echo "       re-run and enter your password when prompted, or:" >&2
      echo "         INSTALL_SERVICE=1 $ROOT/scripts/install.sh" >&2
      exit 1
    fi
    rm -f "$TMP"
    sudo chmod 644 "$UNIT_DST"
    sudo systemctl daemon-reload
    sudo systemctl enable --now ortur-engraver.service
    echo "    status: sudo systemctl status ortur-engraver"
    echo "    UI:     http://127.0.0.1:8000  (LAN if enabled in Settings)"
  fi
else
  echo ""
  echo "OK. Start with:  $ROOT/run.sh"
  if systemd_host; then
    echo "Boot service:    INSTALL_SERVICE=1 $0"
  fi
fi
