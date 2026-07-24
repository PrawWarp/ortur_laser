# Raspberry Pi setup

## Easiest

```bash
curl -fsSL https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.sh | bash
```

Installs system packages if needed, clones to `~/ortur_laser`, first-time `.env` setup (LAN on by default), and starts the UI on port **8000**.

Updates pull new code / deps but **never overwrite** `server/.env`.

Start again later:

```bash
~/ortur_laser/run.sh
```

Update + restart:

```bash
curl -fsSL https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.sh | bash
```

## Boot on startup

On Raspberry Pi / systemd hosts, `install.sh` and `get.sh` **enable and start** `ortur-engraver` by default (keeps running after logout).

```bash
sudo systemctl status ortur-engraver
sudo systemctl restart ortur-engraver
```

Skip the service: `INSTALL_SERVICE=0 ~/ortur_laser/scripts/install.sh`

One-shot enable if you installed earlier without it:

```bash
INSTALL_SERVICE=1 ~/ortur_laser/scripts/install.sh
```

## Permission / ownership fixes

Always run install as your normal user (**not** `sudo ./scripts/install.sh`). Sudo is only prompted for the boot service.

If you see permission errors (often after an earlier sudo install):

```bash
sudo chown -R "$USER:$USER" ~/ortur_laser
~/ortur_laser/scripts/install.sh
```

If only the venv is broken:

```bash
sudo chown -R "$USER:$USER" ~/ortur_laser/server/.venv
# or: rm -rf ~/ortur_laser/server/.venv && ~/ortur_laser/scripts/install.sh
```

The install script tries to add you to `dialout`. If Connect fails after a fresh install:

```bash
sudo usermod -aG dialout $USER
```

Then log out/in or reboot.

## Install only (no auto-start)

```bash
curl -fsSL https://raw.githubusercontent.com/PrawWarp/ortur_laser/main/get.sh | NO_START=1 bash
```

## Optional: SSH deploy key

Only for private forks. See `scripts/setup-deploy-key.sh`.
