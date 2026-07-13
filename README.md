# text-transform-service

A small, self-hosted HTTP service exposing named **text → transform → JSON** operations
backed by local LLM inference (Ollama) with **constrained decoding**. LAN-only,
credential-free by default, single-GPU. It is **not** a general LLM gateway.

Consumers: **Brickfeed News** (`image-prompt`) and the **Scriptorium** bakery
(`cast-mentions`, `cast-canonicalize`, `scene-update`, `illustration-prompt`).

See `text-transform-service-DESIGN.md` for the full design and `text-transform-service-BUILD-PLAN.md`
for the cycle-by-cycle build plan. Decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).

> **Status:** Cycle T5 — the two **Scriptorium cast transforms**, `cast-mentions` (per-page
> character extraction) and `cast-canonicalize` (evidence → paintable canonical description),
> join `image-prompt` (T4) on top of the T3 engine: the full pipeline runs against the model
> with **schema-constrained decoding**, single in-flight generation is serialized (queue →
> `503 busy` on timeout), and `POST /v1/models/unload` frees VRAM. `scene-update` /
> `illustration-prompt` and auth arrive in later cycles (T6+). See [`docs/models.md`](docs/models.md)
> for the resolved model bindings and two Ollama-behaviour findings that shaped the client.

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

### Available transforms

- **`image-prompt`** (production; DESIGN §7.1) — Brickfeed's workload. Send a news story;
  get back one concise, concrete image-generation *subject* prompt (`{"prompt": "..."}`,
  8–60 words, one line, no style/medium/camera words — those are added caller-side). Input
  over the 3000 est-token budget is truncated on paragraph boundaries (`lede_first_n`,
  `meta.truncated: true`). Bound to `qwen3.5:9b`. `options` is `{}`.

  ```bash
  curl -s localhost:8712/v1/transform/image-prompt \
    -H 'content-type: application/json' \
    -d '{"text": "MERIDAN — A magnitude 6.4 earthquake toppled the town clock tower..."}' | jq
  # {"output": {"prompt": "A fallen brick clock tower lies shattered on a cold town square at dawn..."}, "meta": {...}}
  ```

- **`cast-mentions`** (production; DESIGN §7.2) — Scriptorium P1. Send one book page; get back the
  characters mentioned on it with **verbatim** physical descriptors
  (`{"mentions": [{"name", "aliases", "descriptors", "is_person"}, …]}`). Called once per page,
  parallel-safe; the caller reduces mentions across pages. Budget is **`reject`**: a page over the
  1600 est-token budget returns `413 over_budget` (a paginator bug — fail loudly, never truncate).
  Bound to `qwen3.5:9b`. `options` is `{}`.

  ```bash
  curl -s localhost:8712/v1/transform/cast-mentions \
    -H 'content-type: application/json' \
    -d '{"text": "The Time Traveller stood before us, his face ghastly pale..."}' | jq
  # {"output": {"mentions": [{"name": "the Time Traveller", "descriptors": ["his face ghastly pale"], ...}]}, "meta": {...}}
  ```

- **`cast-canonicalize`** (production; DESIGN §7.3) — Scriptorium P2. Called once per major
  character. The evidence rides in `options` (`{"name", "descriptors", "aliases"?, "era"?, "genre"?}`);
  `text` is empty. Returns one paintable canonical entry
  (`{"visual_description", "one_line", "tags"}`) — using only the evidence, choosing plain
  era-appropriate defaults where it is silent. Bound to `qwen3.5:9b`. Missing a required option →
  `400 bad_options`.

  ```bash
  curl -s localhost:8712/v1/transform/cast-canonicalize \
    -H 'content-type: application/json' \
    -d '{"text": "", "options": {"name": "the Time Traveller", "descriptors": ["his face was ghastly pale", "he walked with a limp"]}}' | jq
  # {"output": {"one_line": "A limping, pale Victorian gentleman...", "visual_description": "...", "tags": [...]}, "meta": {...}}
  ```

- **`echo`** — a **dev-only** transform (registered only when `TTS_ENV=dev`) that proves the
  pipeline plumbing against a real model. Not a real workload.

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
