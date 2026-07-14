# Deploying text-transform-service (systemd, the 5070 box)

LAN-only service on port **8712**. These steps install it under systemd so it starts on
boot and restarts on failure. Run them on the RTX 5070 host, where Ollama is already
running. **This is the human's step** — the executor prepares `deploy/`; you run the install.

Prerequisites: [uv](https://docs.astral.sh/uv/) installed, Ollama running, the bound models
pulled (`qwen3.5:9b`, and `qwen3.5:2b` for dev/echo — see [`../docs/models.md`](../docs/models.md)).

## 1. Copy the tree to `/opt`

```bash
sudo mkdir -p /opt/text-transform-service
sudo rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  ./ /opt/text-transform-service/
```

## 2. Build the virtualenv in place

```bash
cd /opt/text-transform-service
sudo uv sync          # creates /opt/text-transform-service/.venv with uvicorn on PATH
```

The unit's `ExecStart` runs `.venv/bin/uvicorn tts.app:app --host 0.0.0.0 --port 8712`, so the
venv must live at `/opt/text-transform-service/.venv` (this is where `uv sync` puts it).

## 3. (Optional) enable auth + set the environment

Auth is **off by default** (LAN posture). To require a shared secret, and to pin the prod
environment, create an env file the unit reads:

```bash
sudo tee /opt/text-transform-service/.env >/dev/null <<'EOF'
TTS_ENV=prod
TRANSFORM_API_KEY=change-me-to-a-real-secret
EOF
sudo chmod 600 /opt/text-transform-service/.env
```

When `TRANSFORM_API_KEY` is set, every `/v1/*` request must carry
`X-Transform-Key: <value>` (ADR-0003); `/health` stays open. Omit the file (or leave the key
unset) to run keyless. All other config vars (`TTS_PORT`, `OLLAMA_URL`, `QUEUE_WAIT_S`,
`TTS_LOG_LEVEL`, …) may also go in this file — see the config table in the top-level README.

## 4. Install and start the unit

```bash
sudo cp /opt/text-transform-service/deploy/text-transform-service.service \
  /etc/systemd/system/text-transform-service.service
sudo systemctl daemon-reload
sudo systemctl enable --now text-transform-service
```

## 5. Verify

```bash
systemctl status text-transform-service --no-pager
curl -s localhost:8712/health | jq          # -> {"status": "ok", ...}
journalctl -u text-transform-service -n 20 --no-pager   # one JSON line per /v1/* request
```

Reboot to confirm it comes back up (`After=ollama.service` orders it behind Ollama):

```bash
sudo reboot
# after login:
curl -s localhost:8712/health | jq
```

## Before you commit to this box — check the unit

`text-transform-service.service` is transcribed from DESIGN §9. **Verify these against the
actual host** and adjust if needed:

- **`User=kris`** — change to the account that owns `/opt/text-transform-service` and may run
  the venv. The service does not need root.
- **Paths** — the `WorkingDirectory`, `ExecStart`, and `EnvironmentFile` all assume
  `/opt/text-transform-service`. Keep them in sync if you install elsewhere.
- **Host/port** — hardcoded `--host 0.0.0.0 --port 8712` in `ExecStart` (matches the DESIGN §9
  defaults). The unit does **not** read `TTS_HOST`/`TTS_PORT` from the env file; edit the
  `ExecStart` line directly to change the bind.
