#!/usr/bin/env bash
# Generate a read-only GitHub deploy key for cloning this repo on a Raspberry Pi.
#
# 1. Run on the Pi (creates ~/.ssh/ortur_laser_deploy)
# 2. Paste the printed public key into GitHub:
#      Repo → Settings → Deploy keys → Add deploy key (Allow write access: OFF)
# 3. Clone once, then use scripts/install.sh for updates:
#      git clone git@github.com:PrawWarp/ortur_laser.git ~/ortur_laser
#      ~/ortur_laser/scripts/install.sh
set -euo pipefail

KEY_PATH="${KEY_PATH:-$HOME/.ssh/ortur_laser_deploy}"
HOST_ALIAS="${HOST_ALIAS:-github.com-ortur}"
REPO_SSH="git@github.com:PrawWarp/ortur_laser.git"
CLONE_DIR="${CLONE_DIR:-$HOME/ortur_laser}"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [[ -f "$KEY_PATH" ]]; then
  echo "Key already exists: $KEY_PATH"
else
  echo "==> generating ed25519 deploy key"
  ssh-keygen -t ed25519 -f "$KEY_PATH" -N "" -C "ortur-laser-pi-deploy"
fi

# SSH config so this key is used only for this repo host alias
CONFIG="$HOME/.ssh/config"
MARKER="# ortur_laser deploy key"
if [[ ! -f "$CONFIG" ]] || ! grep -qF "$MARKER" "$CONFIG" 2>/dev/null; then
  echo "==> appending SSH config ($HOST_ALIAS)"
  cat >>"$CONFIG" <<EOF

$MARKER
Host $HOST_ALIAS
  HostName github.com
  User git
  IdentityFile $KEY_PATH
  IdentitiesOnly yes
EOF
  chmod 600 "$CONFIG"
else
  echo "==> SSH config already has ortur_laser entry"
fi

PUB="$KEY_PATH.pub"
echo ""
echo "========== PUBLIC KEY (add as GitHub Deploy Key) =========="
cat "$PUB"
echo "==========================================================="
echo ""
echo "GitHub: https://github.com/PrawWarp/ortur_laser/settings/keys"
echo "  Title: raspberry-pi (or similar)"
echo "  Key:   paste above"
echo "  Allow write access: NO"
echo ""
echo "Then clone with the host alias (uses this deploy key):"
echo "  git clone git@$HOST_ALIAS:PrawWarp/ortur_laser.git $CLONE_DIR"
echo "  $CLONE_DIR/scripts/install.sh"
echo ""
echo "Optional boot service after install:"
echo "  INSTALL_SERVICE=1 $CLONE_DIR/scripts/install.sh"
echo ""
echo "Test SSH after adding the key:"
echo "  ssh -T git@$HOST_ALIAS"
