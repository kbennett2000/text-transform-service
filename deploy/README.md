# Deploying text-transform-service (systemd, the 5070 box)

LAN-only service on port **8712**. These steps install it under systemd so it starts on
boot and restarts on failure. Run them on the RTX 5070 host, where Ollama is already
running. **This is the human's step** — the executor prepares `deploy/`; you run the install.

Prerequisites: [uv](https://docs.astral.sh/uv/) installed, Ollama running, the bound models
pulled (`qwen3.5:9b`, and `qwen3.5:2b` for dev/echo — see [`../docs/models.md`](../docs/models.md)).

**Redeploying an already-installed box** (steps 1–2 + verify, in one command): run
[`redeploy.sh`](redeploy.sh) as the service user (not root) — it pulls master, rsyncs to `/opt`,
`uv sync`s as the invoking account, restarts the unit, and prints the live transform list.

## 1. Copy the tree to `/opt`

```bash
sudo mkdir -p /opt/text-transform-service
sudo rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  ./ /opt/text-transform-service/
```

## 2. Build the virtualenv in place

Run `uv sync` **as the account the service runs as** (the unit's `User=`, e.g. `kb`) — **not** with
`sudo`:

```bash
cd /opt/text-transform-service
uv sync               # as the User= account; creates ./.venv with uvicorn on PATH
```

**Do not `sudo uv sync`.** When uv has no system Python bound it provisions its own CPython under the
*invoking* user's home; run as root that lands in `/root/.local/share/uv/python/…` (mode 700), which the
unprivileged `User=` service cannot exec — systemd then fails the unit with `status=203/EXEC`. Building
the venv as the service account puts the interpreter under that account's home, where the service reaches it.

The `rsync -a` in step 1 preserves the source tree's ownership, so `/opt/text-transform-service` is owned
by the copying user and `uv sync` can write `.venv` in place without sudo. (If the tree is root-owned,
`sudo chown -R <user>:<user> /opt/text-transform-service` first.)

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
`MAX_QUEUE_DEPTH`, `TTS_PRIMARY_MODEL`, `TTS_LOG_LEVEL`, …) may also go in this file — see the
config table in the developer guide. For a *readiness* check (model actually loaded, not just
Ollama reachable) poll `GET /ready` or `/health`'s additive `ready` flag; both stay open like
`/health` (T14).

## 3a. Ollama host binding — required for large opinion-gate batches (cycle T13)

`opinion-gate` classifies batches of up to ~100 candidates in one call, which needs a large
context window. At that size, `qwen3.5:9b` with the default **f16** KV cache silently drops the
tail of the verdict array (a completeness bug, not an error) — the caller's fail-closed rule then
over-excludes those stories. Quantizing the KV cache to **q8_0** fixes it (and shrinks the KV
cache, keeping the model 100% on-GPU). This is a **host-wide Ollama setting**, applied to the
`ollama.service` unit via a drop-in — **not** a TTS config var. See
[`../docs/models.md`](../docs/models.md) for the evidence.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo cp /opt/text-transform-service/deploy/ollama.service.d/flash-attn.conf \
  /etc/systemd/system/ollama.service.d/flash-attn.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify both vars are live (and note `OLLAMA_FLASH_ATTENTION=1` is **mandatory** — llama.cpp
refuses V-cache quantization without flash attention, segfaulting on load):

```bash
systemctl show ollama.service -p Environment   # -> OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0
```

This is safe for the other transforms (their contexts are tiny; q8_0 KV is imperceptible for these
extraction tasks). Skipping it does not break startup, but large opinion-gate batches will
silently return incomplete verdict sets.

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

- **`User=`** — set to the account that owns `/opt/text-transform-service` and built the venv (this repo
  ships `User=kb`; adjust if your account differs). It must match the user that ran `uv sync` in step 2,
  or the service can't exec the venv's Python (see the `203/EXEC` note above). The service does not need root.
- **Paths** — the `WorkingDirectory`, `ExecStart`, and `EnvironmentFile` all assume
  `/opt/text-transform-service`. Keep them in sync if you install elsewhere.
- **Host/port** — hardcoded `--host 0.0.0.0 --port 8712` in `ExecStart` (matches the DESIGN §9
  defaults). The unit does **not** read `TTS_HOST`/`TTS_PORT` from the env file; edit the
  `ExecStart` line directly to change the bind.
