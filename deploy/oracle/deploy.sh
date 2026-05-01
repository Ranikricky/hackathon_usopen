#!/usr/bin/env bash
set -euo pipefail

REMOTE="${1:-}"
KEY_PATH="${HORIZONXL_SSH_KEY:-$HOME/.ssh/horizonxl_oci_ed25519}"
REMOTE_DIR="${HORIZONXL_REMOTE_DIR:-horizonxl}"

if [[ -z "$REMOTE" ]]; then
  echo "Usage: HORIZONXL_SSH_KEY=~/.ssh/horizonxl_oci_ed25519 ./deploy/oracle/deploy.sh ubuntu@<PUBLIC_IP>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ssh -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$REMOTE" 'bash -s' < "$ROOT_DIR/deploy/oracle/bootstrap.sh"

rsync -az --delete \
  -e "ssh -i $KEY_PATH -o StrictHostKeyChecking=accept-new" \
  --exclude '.git' \
  --exclude '.vercel' \
  --exclude 'node_modules' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/dist' \
  --exclude 'backend/.venv' \
  --exclude 'backend/uploads/reports' \
  --exclude 'backend/uploads/simulations' \
  "$ROOT_DIR/" "$REMOTE:$REMOTE_DIR/"

ssh -i "$KEY_PATH" "$REMOTE" "
  cd '$REMOTE_DIR' &&
  docker compose -f docker-compose.prod.yml up -d --build --remove-orphans &&
  docker ps --filter name=horizonxl
"

echo "Horizon XL deployed. Open: http://${REMOTE#*@}"
