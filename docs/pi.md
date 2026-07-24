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

```bash
INSTALL_SERVICE=1 ~/ortur_laser/scripts/install.sh
sudo systemctl status ortur-engraver
```

## Serial (USB) access

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
