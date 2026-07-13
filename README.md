# text-transform-service

A small, self-hosted HTTP service exposing named **text → transform → JSON** operations
backed by local LLM inference (Ollama) with **constrained decoding**. LAN-only,
credential-free by default, single-GPU. It is **not** a general LLM gateway.

Consumers: **Brickfeed News** (`image-prompt`) and the **Scriptorium** bakery
(`cast-mentions`, `cast-canonicalize`, `scene-update`, `illustration-prompt`).

See `text-transform-service-DESIGN.md` for the full design and `text-transform-service-BUILD-PLAN.md`
for the cycle-by-cycle build plan. Decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).

> **Status:** Cycle T2 — the full request pipeline (registry, budget/truncation,
> validators, retry, error taxonomy) runs end-to-end against a fake LLM, exposed via
> `POST /v1/transform/{name}` and proven with the dev-only `echo` transform. Real Ollama
> generation, production transforms, and auth arrive in later cycles (T3+).

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

## Transforms — `POST /v1/transform/{name}`

Send text; get back schema-constrained JSON plus request metadata.

```bash
curl -s localhost:8712/v1/transform/echo \
  -H 'content-type: application/json' \
  -d '{"text": "First sentence. Second sentence.", "options": {}}' | jq
```

`options` is transform-specific (validated against the transform's `options_schema`);
omitting it means `{}`. A success is `200`:

```json
{
  "output": { "echo": "First sentence." },
  "meta": {
    "transform": "echo", "transform_version": "0.1.0", "model": "qwen3:0.6b",
    "input_tokens_est": 6, "truncated": false, "attempts": 1,
    "latency_ms": 3, "queued_ms": 0
  }
}
```

Errors always use `{"error": {"code": "...", "message": "...", "detail": {...}}}`
(DESIGN §4): `400 bad_request`/`bad_options`, `404 unknown_transform`, `413 over_budget`,
`422 validation_failed` (generation failed validators after retries), `503 busy`/
`model_unavailable`, `500 internal`. **Error codes are API — a change is a breaking change.**

`echo` is a **dev-only** transform (registered only when `TTS_ENV=dev`) that proves the
pipeline plumbing. In T2 there is no real generation backend yet, so a live POST returns
`503 model_unavailable`; the real Ollama client arrives in T3.

## Configuration (env, all optional)

| Var | Default | Meaning |
|---|---|---|
| `TTS_PORT` | `8712` | Bind port |
| `TTS_HOST` | `0.0.0.0` | LAN bind |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama runtime |
| `OLLAMA_KEEP_ALIVE` | `5m` | Passed on every generate |
| `TRANSFORM_API_KEY` | unset | Enables shared-secret auth when set |
| `QUEUE_WAIT_S` | `90` | Generation queue timeout |
| `TTS_LOG_LEVEL` | `INFO` | Log level |
| `TTS_ENV` | `prod` | `dev` enables dev-only transforms (`echo`) |

(All of the above except `TTS_ENV` are the DESIGN §9 table; `TTS_ENV` is a T2 addition
for the dev gate.)

## Development

```bash
make test        # non-GPU suite (Ollama mocked) — runs anywhere
make test-gpu    # GPU suite — run only on the 5070 with Ollama up
make lint        # ruff
```
