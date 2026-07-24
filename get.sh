#!/usr/bin/env bash
# One-shot install + start for Linux / Raspberry Pi.
#
#   curl -fsSL https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.sh | bash
#
# Env: DIR=~/ortur_laser  BRANCH=main  NO_START=1  INSTALL_SERVICE=1
set -euo pipefail

REPO_HTTPS="${REPO_HTTPS:-https://github.com/PrawWarp/ortur_laser.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/ortur_laser}"

echo "==> Ortur Engraver — easy install"
echo "    target: $DIR"

install_pkgs() {
  local missing=()
  command -v git >/dev/null 2>&1 || missing+=(git)
  command -v python3 >/dev/null 2>&1 || missing+=(python3)
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import venv" 2>/dev/null || missing+=(python3-venv)
  else
    missing+=(python3-venv)
  fi
  command -v pip3 >/dev/null 2>&1 || missing+=(python3-pip)

  [[ ${#missing[@]} -eq 0 ]] && return 0

  echo "==> installing packages: ${missing[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y "${missing[@]}"
  else
    echo "error: missing ${missing[*]}. Install them, then re-run." >&2
    exit 1
  fi
}

install_pkgs

if [[ -d "$DIR/.git" ]]; then
  echo "==> updating existing clone"
  git -C "$DIR" fetch --prune origin
  git -C "$DIR" checkout "$BRANCH"
  git -C "$DIR" pull --ff-only origin "$BRANCH"
elif [[ -e "$DIR" ]]; then
  echo "error: $DIR exists but is not a git repo. Pick another DIR=..." >&2
  exit 1
else
  echo "==> cloning"
  git clone --branch "$BRANCH" "$REPO_HTTPS" "$DIR"
fi

chmod +x "$DIR"/get.sh "$DIR"/run.sh "$DIR"/scripts/*.sh 2>/dev/null || true

SKIP_GIT=1 bash "$DIR/scripts/install.sh"

if [[ "${NO_START:-0}" == "1" ]]; then
  echo ""
  echo "Installed. Start anytime with:  $DIR/run.sh"
  if [[ -d /run/systemd/system ]]; then
    echo "Or boot service:  INSTALL_SERVICE=1 $DIR/scripts/install.sh"
  fi
  exit 0
fi

# Prefer systemd unit when install enabled it (default on Pi).
if command -v systemctl >/dev/null 2>&1 && systemctl cat ortur-engraver.service >/dev/null 2>&1; then
  echo ""
  echo "==> starting boot service (ortur-engraver)"
  sudo systemctl restart ortur-engraver.service
  echo "    http://127.0.0.1:8000"
  echo "    status: sudo systemctl status ortur-engraver"
  echo "    stop:   sudo systemctl stop ortur-engraver"
  exit 0
fi

echo ""
echo "==> starting UI (Ctrl+C to stop)"
echo "    http://127.0.0.1:8000"
exec "$DIR/run.sh"
