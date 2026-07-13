# text-transform-service

A small, self-hosted HTTP service exposing named **text → transform → JSON** operations
backed by local LLM inference (Ollama) with **constrained decoding**. LAN-only,
credential-free by default, single-GPU. It is **not** a general LLM gateway.

Consumers: **Brickfeed News** (`image-prompt`) and the **Scriptorium** bakery
(`cast-mentions`, `cast-canonicalize`, `scene-update`, `illustration-prompt`).

See `text-transform-service-DESIGN.md` for the full design and `text-transform-service-BUILD-PLAN.md`
for the cycle-by-cycle build plan. Decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).

> **Status:** Cycle T3 — real Ollama generation with **schema-constrained decoding** is
> live. `POST /v1/transform/{name}` runs the full pipeline against the model, single
> in-flight generation is serialized (queue → `503 busy` on timeout), and
> `POST /v1/models/unload` frees VRAM. Production transforms and auth arrive in later
> cycles (T4+). See [`docs/models.md`](docs/models.md) for the resolved model bindings and
> two Ollama-behaviour findings that shaped the client.

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
  "models_loaded": ["qwen3.5:9b"],
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
    "transform": "echo", "transform_version": "0.1.0", "model": "qwen3.5:2b",
    "input_tokens_est": 6, "truncated": false, "attempts": 1,
    "latency_ms": 3967, "queued_ms": 0
  }
}
```

Errors always use `{"error": {"code": "...", "message": "...", "detail": {...}}}`
(DESIGN §4): `400 bad_request`/`bad_options`, `404 unknown_transform`, `413 over_budget`,
`422 validation_failed` (generation failed validators after retries), `503 busy`/
`model_unavailable`, `500 internal`. **Error codes are API — a change is a breaking change.**

`echo` is a **dev-only** transform (registered only when `TTS_ENV=dev`) that proves the
pipeline plumbing against a real model.

Output is **schema-constrained**: the transform's `output_schema` is passed to Ollama as a
grammar (`format`) *and* re-validated after generation; on validator failure the pipeline
retries with a temperature bump before returning `422`. Qwen3.5 "thinking" is disabled
(`think: false`) — it is pure latency for these extraction transforms.

## Unloading models — `POST /v1/models/unload`

Frees model VRAM (the endpoint the Scriptorium orchestrator calls before a render phase).
Body `{"model": "qwen3.5:9b"}` targets one; `{}` unloads everything currently loaded. Each
target is unloaded (`keep_alive: 0`) and the response reports models confirmed gone via
`/api/ps`:

```bash
curl -s -X POST localhost:8712/v1/models/unload \
  -H 'content-type: application/json' -d '{}' | jq
# {"unloaded": ["qwen3.5:2b"]}
```

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

**Model bindings** (see [`docs/models.md`](docs/models.md)): the default per-transform model
is `qwen3.5:9b`; the fast test/CI model (and `echo`'s binding) is `qwen3.5:2b`. These were
rebound in T3 from the absent DESIGN §2 tags (`qwen3:8b` / `qwen3:0.6b`) to the same weight
classes in the installed `qwen3.5` family.

## Development

```bash
make test        # non-GPU suite (Ollama mocked) — runs anywhere
make test-gpu    # GPU suite — run only on the 5070 with Ollama up
make lint        # ruff
```
