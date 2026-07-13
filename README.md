# text-transform-service

A small, self-hosted HTTP service exposing named **text → transform → JSON** operations
backed by local LLM inference (Ollama) with **constrained decoding**. LAN-only,
credential-free by default, single-GPU. It is **not** a general LLM gateway.

Consumers: **Brickfeed News** (`image-prompt`) and the **Scriptorium** bakery
(`cast-mentions`, `cast-canonicalize`, `scene-update`, `illustration-prompt`).

See `text-transform-service-DESIGN.md` for the full design and `text-transform-service-BUILD-PLAN.md`
for the cycle-by-cycle build plan. Decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).

> **Status:** Cycle T1 — scaffold, ADRs, and `/health` only. Transforms, the request
> pipeline, generation, and auth arrive in later cycles.

## Requirements

- Python 3.12 (managed by [uv](https://docs.astral.sh/uv/))
- [Ollama](https://ollama.com/) running locally (default `http://127.0.0.1:11434`)

## Run

```bash
uv sync          # install deps into .venv
make dev         # serve on 0.0.0.0:8712 with auto-reload
```

Then:

```bash
curl -s localhost:8712/health | jq
```

`/health` reports service and Ollama status. It never fails: if Ollama is down it
returns `200` with `status: "degraded"` rather than erroring.

```json
{
  "status": "ok",
  "ollama_reachable": true,
  "models_loaded": ["qwen3:8b"],
  "uptime_s": 8641
}
```

## Configuration (env, all optional — DESIGN §9)

| Var | Default | Meaning |
|---|---|---|
| `TTS_PORT` | `8712` | Bind port |
| `TTS_HOST` | `0.0.0.0` | LAN bind |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama runtime |
| `OLLAMA_KEEP_ALIVE` | `5m` | Passed on every generate |
| `TRANSFORM_API_KEY` | unset | Enables shared-secret auth when set |
| `QUEUE_WAIT_S` | `90` | Generation queue timeout |
| `TTS_LOG_LEVEL` | `INFO` | Log level |

## Development

```bash
make test        # non-GPU suite (Ollama mocked) — runs anywhere
make test-gpu    # GPU suite — run only on the 5070 with Ollama up
make lint        # ruff
```
