#!/usr/bin/env bash
# deploy/redeploy.sh — one-command redeploy of text-transform-service to /opt on the 5070 box.
# Encodes deploy/README.md steps 1 (pull) → 2 (rsync + uv sync + restart) → 5 (verify).
#
# Run as the service user (the unit's User=, e.g. kb), NEVER as root:
#   uv sync as root provisions its CPython under /root (mode 700), which the unprivileged
#   User= service cannot exec -> systemd fails the unit with status=203/EXEC (README §2).
# sudo is invoked internally only for the rsync-to-/opt and the systemctl restart; it will
# prompt for your password on the terminal. Idempotent: safe to re-run.
set -euo pipefail

DEST=/opt/text-transform-service
SERVICE=text-transform-service
PORT=8712

# --- refuse to run as root: the uv sync step must run as the service account (see above).
if [ "$(id -u)" -eq 0 ]; then
  echo "refusing to run as root: uv sync must run as the service user (User=kb), not root." >&2
  echo "run:  deploy/redeploy.sh   (sudo is invoked internally for rsync + restart)" >&2
  exit 1
fi

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"

# --- step 1: pull current merged master, show the transforms that will ship.
git checkout master
git pull --ff-only origin master
echo "== transforms in source tree =="
ls src/tts/transforms/*.py

# --- step 2: copy the tree to /opt, build the venv as the (non-root) service user, restart.
sudo rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  ./ "$DEST/"
( cd "$DEST" && uv sync )            # as the invoking non-root service user; NEVER sudo
sudo systemctl restart "$SERVICE"

# --- verify: unit active, health answers (degraded is OK when Ollama is down), transforms live.
systemctl status "$SERVICE" --no-pager | head -5 || true

# uvicorn takes a moment to bind after restart; poll /health before verifying (avoids a race).
for _ in $(seq 1 20); do
  curl -sf "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

echo "== /health =="
curl -s "localhost:$PORT/health" | jq . || true
echo "== /v1/transforms (live registry) =="
curl -s "localhost:$PORT/v1/transforms" | jq -r '.transforms[].name' | sort
