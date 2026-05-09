#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu 22.04+ VPS.
#
# Usage (as a non-root user with sudo):
#   curl -L https://raw.githubusercontent.com/zachevansss/Claude-Code-Test/main/backend/deploy/install.sh | bash
#
# Or after cloning manually:
#   bash backend/deploy/install.sh
#
# What it does:
#   1. Installs system deps (Python 3.12, git, build tools)
#   2. Clones the repo into ~/copytrade if not already present
#   3. Creates a Python venv and installs requirements
#   4. Creates an empty .env from the live template (operator fills it in next)
#   5. Sets up the daily-backup directory
#
# After this script: edit ~/copytrade/backend/.env, then install the systemd unit.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/zachevansss/Claude-Code-Test.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/copytrade}"

echo "==> Updating apt and installing base deps"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  software-properties-common \
  curl ca-certificates git build-essential

echo "==> Installing Python 3.12 (deadsnakes PPA)"
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev

echo "==> Cloning repo to $INSTALL_DIR"
if [ ! -d "$INSTALL_DIR" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "   (already present, pulling latest)"
  git -C "$INSTALL_DIR" pull --ff-only
fi

cd "$INSTALL_DIR/backend"

echo "==> Creating venv at backend/.venv"
python3.12 -m venv .venv
./.venv/bin/pip install --upgrade pip wheel
./.venv/bin/pip install -r requirements.txt

echo "==> Seeding .env from .env.live.example (if .env not present)"
if [ ! -f .env ]; then
  cp deploy/.env.live.example .env
  echo "   .env created — EDIT IT before starting the bot:"
  echo "     nano $INSTALL_DIR/backend/.env"
  echo "   Required fields: MASTER_ENCRYPTION_KEY, JWT_SECRET, POLYGON_RPC_URL"
fi

echo "==> Creating backups directory"
mkdir -p "$INSTALL_DIR/backend/backups"

echo "==> Verifying outbound IP is non-US"
echo -n "   Country code: "
curl -s https://ipinfo.io/country || echo "(could not fetch)"
echo "   ^ if this prints 'US' you must use a VPN or non-US VPS region before going live."

echo ""
echo "==> Done."
echo ""
echo "Next steps:"
echo "  1. Edit backend/.env (fill in MASTER_ENCRYPTION_KEY, JWT_SECRET, POLYGON_RPC_URL)"
echo "     - generate the master key: cd backend && ./.venv/bin/python -m src.wallet.crypto generate"
echo "     - back up the master key to a password manager BEFORE going live"
echo "  2. Install the systemd unit:"
echo "       sudo cp backend/deploy/copytrade.service /etc/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now copytrade"
echo "  3. Watch logs:    sudo journalctl -u copytrade -f"
echo "  4. Follow the launch checklist: backend/deploy/launch.md"
