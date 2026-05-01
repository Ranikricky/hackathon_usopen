#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  curl -fsSL https://get.docker.com | sudo sh
fi

sudo usermod -aG docker "$USER" || true

if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow OpenSSH || true
  sudo ufw allow 80/tcp || true
  sudo ufw allow 443/tcp || true
fi

mkdir -p "$HOME/horizonxl"

echo "Oracle VM bootstrap complete. If Docker permissions fail, log out and back in, then rerun deploy."
