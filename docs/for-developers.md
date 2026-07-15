# Developer guide — text-transform-service

> ← Back to the [project README](../README.md) · This is the technical reference for developers who want
> to **use or extend** the service. For a maximum-density summary aimed at AI agents, see
> [ai-reference.md](ai-reference.md).

A small, self-hosted HTTP service exposing named **text → transform → JSON** operations backed by local
LLM inference (Ollama) with **constrained decoding**. LAN-only, credential-free by default, single-GPU.
It is **not** a general LLM gateway.

Consumers: **Brickfeed News** (`image-prompt`, `story-cover`, `opinion-gate`,
`opinion-image-brief`) and the **Scriptorium** bakery (`cast-mentions`, `cast-canonicalize`,
`scene-update`, `illustration-prompt`).

See [`text-transform-service-DESIGN.md`](../text-transform-service-DESIGN.md) for the full design and
[`text-transform-service-BUILD-PLAN.md`](../text-transform-service-BUILD-PLAN.md) for the cycle-by-cycle
build plan. Decisions are recorded as ADRs in [`adr/`](adr/).

> **Status:** the service is **feature-complete for M1** and deployed under systemd on the 5070.
> It exposes `GET /v1/transforms` (registry listing), optional shared-secret **auth**
> (`X-Transform-Key`, off by default), structured **per-request JSON logging** with an `X-Request-Id`
> header, and a committed systemd unit under [`../deploy/`](../deploy/). All transforms shipped:
> `image-prompt` (Brickfeed), `cast-mentions` + `cast-canonicalize`, and `scene-update` +
> `illustration-prompt` — **every Scriptorium bake transform** — plus the soft-validator `meta.warnings`
> mechanism (see below). The pipeline runs with **schema-constrained decoding**; single in-flight
> generation is serialized (queue → `503 busy` on timeout); `POST /v1/models/unload` frees VRAM. See
> [`models.md`](models.md) for the resolved model bindings and two Ollama-behaviour findings that shaped
> the client.

## API summary

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /health` | never | Service + Ollama status (+ additive `ready`); never 500s |
| `GET /ready` | never | True model readiness: `ready` iff the primary model is resident; never 500s |
| `GET /v1/transforms` | when enabled | List registered transforms + their JSON Schemas |
| `POST /v1/transform/{name}` | when enabled | Run a named transform: text → schema-constrained JSON |
| `POST /v1/models/unload` | when enabled | Free model VRAM (`{"model": "..."}` or `{}` for all) |

Auth is off by default (LAN posture) and enabled by setting `TRANSFORM_API_KEY` — see
[Authentication](#authentication). Every response carries an `X-Request-Id` header, and each
`/v1/*` request is logged as one structured JSON line — see [Operability](#operability).

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
  "ready": true,
  "ollama_reachable": true,
  "models_loaded": ["qwen3.5:9b"],
  "uptime_s": 8641
}
```

`status` is `"ok"` iff Ollama's `/api/ps` answered — liveness. The additive `ready` flag
(T14) is *readiness*: true iff the primary model (`TTS_PRIMARY_MODEL`, default `qwen3.5:9b`)
is actually resident. The two differ right after a `/v1/models/unload`: Ollama still answers
(`status:"ok"`) but nothing is loaded (`ready:false`). Poll `GET /ready` for readiness alone:

```bash
curl -s localhost:8712/ready | jq
# { "ready": false, "ollama_reachable": true, "models_loaded": [],
#   "primary_model": "qwen3.5:9b", "uptime_s": 8641 }
```

`/ready` never 500s either — Ollama down is `ready:false`, not an error.

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
(DESIGN §4): `400 bad_request`/`bad_options`, `401 unauthorized` (auth enabled, key
missing/wrong), `404 unknown_transform`, `413 over_budget`, `422 validation_failed`
(generation failed validators after retries), `503 busy`/`model_unavailable`, `500 internal`.
**Error codes are API — a change is a breaking change.**

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

- **`story-cover`** (production; Brickfeed request §1, T9) — Brickfeed's story cover bundle. Send a
  story's source context (title/publisher/URL); get back five fields: an original `headline`
  (10–200), a neutral `description` (40–600), a subject-only `imagePrompt` (30–400, 8–60 words, no
  style/medium words), a `category` (one of the fixed 8-value enum), and a one-line `caption`
  (15–160). Style — including Brickfeed's toy-brick treatment — is applied caller-side, never baked
  in (ADR-0004). Bound to `qwen3.5:9b`. `options` is `{}`. Budget is `truncate`, but the
  single-paragraph input means truncation is a no-op (long titles pass through; nothing is rejected).

  ```bash
  curl -s localhost:8712/v1/transform/story-cover \
    -H 'content-type: application/json' \
    -d '{"text": "Source article title: City council approves new bike lane network downtown\nPublisher: Metro Herald\nSource URL: https://example.com/bike-lanes"}' | jq
  # {"output": {"headline": "...", "description": "...", "imagePrompt": "...", "category": "BUSINESS", "caption": "..."}, "meta": {...}}
  ```

- **`opinion-gate`** (production; Brickfeed request §2, T10) — an **editorial safety gate**. Send a
  JSON array of candidate stories `[{id, title, summary}]`; get back one `verdict` per story
  (`eligible` / `excluded` / `uncertain`) with a short `reason`, deciding whether each is safe to
  treat as gentle satire. Admitted under **ADR-0007** (a safety classifier, otherwise excluded by
  §1). **The service is fail-loud and the caller must fail-closed:** treat every 4xx/5xx, every
  `uncertain` verdict, and any missing/duplicate `id` as *exclude*. `verdict` is the sole decision
  field. Budget is **`reject`**: a candidate list over 1600 est-tokens returns `413 over_budget`
  (never silently drop candidates). Bound to `qwen3.5:9b`. `options` is `{}`.

  ```bash
  curl -s localhost:8712/v1/transform/opinion-gate \
    -H 'content-type: application/json' \
    -d '{"text": "[{\"id\":\"a1\",\"title\":\"Town pumpkin smashes record\",\"summary\":\"A 2,300-pound pumpkin took the fair title.\"},{\"id\":\"b2\",\"title\":\"Fatal crash closes interstate\",\"summary\":\"Several died in a pileup.\"}]"}' | jq
  # {"output": {"verdicts": [{"id": "a1", "verdict": "eligible", "reason": "..."}, {"id": "b2", "verdict": "excluded", "reason": "..."}]}, "meta": {...}}
  ```

- **`opinion-image-brief`** (production; Brickfeed request §4, T10) — send a finished opinion piece
  (title + body) plus subject context; get back a subject-only `imagePrompt` (30–400, 8–60 words)
  and one-line `caption` (15–160) illustrating the piece's **subject** — never the author or the act
  of writing. Style (incl. toy-brick) stays caller-side (ADR-0004). Input over the 3000 est-token
  budget is truncated (`head`, keeping the leading piece). Bound to `qwen3.5:9b`. `options` is `{}`.

  ```bash
  curl -s localhost:8712/v1/transform/opinion-image-brief \
    -H 'content-type: application/json' \
    -d '{"text": "Title: The Tyranny of the Two-Wheeled Elite\n\nIt has come to my attention that our council...\n\nSubject context:\nARTICLE 1: Downtown gains protected bike lanes."}' | jq
  # {"output": {"imagePrompt": "...", "caption": "..."}, "meta": {...}}
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

- **`scene-update`** (production; DESIGN §7.4) — Scriptorium P3. Called once per page **strictly in
  order**: send the page plus the previous page's ledger, get back the updated rolling scene ledger
  and a per-page selection signal
  (`{"location", "time_of_day", "atmosphere", "present", "scene_changed", "visual_salience", "best_visual_beat", "carry_notes"}`).
  `options` is `{"prior_ledger": <object|null>, "cast_names": [...], "era"?}` — `prior_ledger` is `null`
  on page 1, then each call's ledger is threaded into the next. Budget is **`reject`** (page over the
  1600 est-token budget → `413 over_budget`). Bound to `qwen3.5:9b`.

  ```bash
  curl -s localhost:8712/v1/transform/scene-update \
    -H 'content-type: application/json' \
    -d '{"text": "The Time Traveller sat by the fire, turning the little brass machine...", "options": {"prior_ledger": null, "cast_names": ["the Time Traveller", "Filby"], "era": "1890s"}}' | jq
  # {"output": {"location": "the Time Traveller's smoking-room", "visual_salience": 0.65, "best_visual_beat": "...", ...}, "meta": {...}}
  ```

- **`illustration-prompt`** (production; DESIGN §7.5) — Scriptorium P5. Called once per **selected** page.
  Send the page, its ledger, and the cast entries for characters present; get back one neutral SDXL
  *subject* prompt weaving each depicted character's visual identifiers in
  (`{"prompt", "depicted", "shot", "avoid"?}`). `options` is
  `{"ledger": <object>, "cast": [{"name", "one_line"}, …], "era"?}`. Style/medium words are caller-side;
  their appearance is drift (`422`). The `depicted ⊆ cast` check is a **soft** validator — a stray name
  is recorded to `meta.warnings` (below), not a failure. Bound to `qwen3.5:9b`.

  ```bash
  curl -s localhost:8712/v1/transform/illustration-prompt \
    -H 'content-type: application/json' \
    -d '{"text": "...page...", "options": {"ledger": {"best_visual_beat": "The model machine vanishes.", "location": "the smoking-room"}, "cast": [{"name": "the Time Traveller", "one_line": "a pale, grey-haired Victorian gentleman"}]}}' | jq
  # {"output": {"prompt": "...", "depicted": ["the Time Traveller"], "shot": "medium"}, "meta": {...}}
  ```

- **`echo`** — a **dev-only** transform (registered only when `TTS_ENV=dev`) that proves the
  pipeline plumbing against a real model. Not a real workload.

Output is **schema-constrained**: the transform's `output_schema` is passed to Ollama as a
grammar (`format`) *and* re-validated after generation; on validator failure the pipeline
retries with a temperature bump before returning `422`. Qwen3.5 "thinking" is disabled
(`think: false`) — it is pure latency for these extraction transforms.

### Soft warnings — `meta.warnings`

Most validators are **hard**: a violation retries and ultimately returns `422`. A few checks are
advisory — a mild drift the caller may want to know about but which shouldn't fail the request
(e.g. `illustration-prompt`'s `depicted ⊆ cast` check). These are **soft validators**: they add a
string to `meta.warnings` and the request still succeeds with `200`.

`meta.warnings` is **present only when a soft finding fired** — it is absent on the common (clean)
path, so the success `meta` shape is otherwise exactly as shown above. Consumers should read it
defensively (`meta.get("warnings")`), not assume the key exists.

```json
"meta": {
  "transform": "illustration-prompt", "...": "...",
  "warnings": ["depicted not in cast: ['Filby']"]
}
```

## Listing transforms — `GET /v1/transforms`

Serializes the registry so a caller can discover what is available and validate against the
schemas. Each entry carries the caller-facing fields plus both JSON Schemas; the internal
prompt template and Python validators are never exposed.

```bash
curl -s localhost:8712/v1/transforms | jq
# { "transforms": [
#   { "name": "cast-canonicalize", "version": "0.1.0", "model": "qwen3.5:9b",
#     "input_budget": 1200, "over_budget": "truncate",
#     "options_schema": { ... }, "output_schema": { ... } },
#   ...
# ] }
```

## Unloading models — `POST /v1/models/unload`

Frees model VRAM (the endpoint the Scriptorium orchestrator calls before a render phase).
Body `{"model": "qwen3.5:9b"}` targets one; `{}` unloads everything currently loaded. Each
target is unloaded (`keep_alive: 0`) and the response reports models confirmed gone via
`/api/ps`. The eviction is performed while holding the generation slot (T14), so it can't
race an in-flight transform; and because transforms reload the model on demand, an ill-timed
unload just makes the next transform slower, never `503 model_unavailable`.

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
| `QUEUE_WAIT_S` | `90` | Generation queue timeout (max wait for the slot) → `503 busy` |
| `MAX_QUEUE_DEPTH` | `0` | Max requests allowed to wait for the slot; `0` = unbounded. When set, a request arriving with the queue full fast-fails `503 busy` (T14) |
| `TTS_PRIMARY_MODEL` | `qwen3.5:9b` | Model whose residency defines readiness for `/ready` and `/health.ready` (T14) |
| `TTS_LOG_LEVEL` | `INFO` | Log level |
| `TTS_ENV` | `prod` | `dev` enables dev-only transforms (`echo`) |

(The DESIGN §9 table plus later additions: `TTS_ENV` (T2 dev gate) and `MAX_QUEUE_DEPTH` /
`TTS_PRIMARY_MODEL` (T14 concurrency-burst reliability, ADR-0008).)

**Model bindings** (see [`models.md`](models.md)): the default per-transform model
is `qwen3.5:9b`; the fast test/CI model (and `echo`'s binding) is `qwen3.5:2b`. These were
rebound in T3 from the absent DESIGN §2 tags (`qwen3:8b` / `qwen3:0.6b`) to the same weight
classes in the installed `qwen3.5` family. `GET /v1/transforms` reports each transform's
actual binding.

## Authentication

Auth is **optional and off by default** (LAN posture, ADR-0003). Set `TRANSFORM_API_KEY` and
every `/v1/*` request must then carry that value in an `X-Transform-Key` header; `/health` is
always open. A missing or wrong key is `401 unauthorized` in the standard error envelope.

```bash
export TRANSFORM_API_KEY=change-me
curl -s localhost:8712/v1/transforms                              # -> 401 unauthorized
curl -s localhost:8712/v1/transforms -H 'X-Transform-Key: change-me'   # -> 200
curl -s localhost:8712/health                                    # -> 200 (never gated)
```

Leaving `TRANSFORM_API_KEY` unset disables auth entirely — no header required.

## Operability

Every response carries an `X-Request-Id` header (uuid4 hex short). Each `/v1/*` request is
logged as exactly one structured JSON line on the `tts.request` logger (DESIGN §9); `/health`
is intentionally excluded (it is polled frequently). Fields:
`ts, request_id, transform, status`, plus `attempts, input_tokens_est, truncated, queued_ms,
latency_ms` on a completed pipeline run and `error_code` on failures.

```json
{"ts": "2026-07-13T18:22:04.117+00:00", "request_id": "9f3a1c07", "transform": "scene-update",
 "status": 200, "attempts": 1, "input_tokens_est": 812, "truncated": false,
 "queued_ms": 0, "latency_ms": 7434}
```

Log level comes from `TTS_LOG_LEVEL`. Under systemd the lines land in `journalctl -u
text-transform-service`. Deployment steps: [`../deploy/README.md`](../deploy/README.md).

## Adding a transform

A transform is a Python module in [`src/tts/transforms/`](../src/tts/transforms/) that builds a
frozen `Transform` (see [`registry.py`](../src/tts/registry.py)). The 8-step recipe:

1. **Module** — add `src/tts/transforms/<name>.py` with a `build_<name>() -> Transform`.
2. **Schemas** — write the `output_schema` (JSON Schema, passed to Ollama as a grammar *and*
   re-validated) and the `options_schema` (validated → `400 bad_options` on failure).
3. **Template** — write the DESIGN §7 prompt verbatim in `SYSTEM: … USER: …` form; `render_messages`
   splits it and substitutes `{common framing}`. Split any >100-char line into adjacent literals
   (no newline at the join → byte-identical render) to satisfy ruff without a `version` bump.
4. **Validators** — compose from [`validators.py`](../src/tts/validators.py) (e.g. `word_range`,
   `banned_substrings`, `no_empty_strings`). A validator returns a reason string to fail (retry →
   `422`), `None` to pass, or a `"warn:<reason>"` string for a non-fatal `meta.warnings` entry. An
   options-aware validator sets `wants_options = True` and is called `validator(output, options)`.
5. **Register** — add `register(build_<name>())` in
   [`transforms/__init__.py`](../src/tts/transforms/__init__.py) (`echo` stays dev-gated; production
   transforms register unconditionally).
6. **Fixtures** — add realistic inputs under `tests/fixtures/<domain>/` (public-domain text).
7. **Unit tests** — drive the real `build_<name>()` with `FakeLLMClient` (schema-retry, budget,
   validators, options shape). **Never assert exact model wording** — schema/shape/bounds only.
8. **GPU test** — add a `@pytest.mark.gpu` case in [`tests/test_gpu.py`](../tests/test_gpu.py) that runs
   the real binding through `run_transform` and prints outputs for the CYCLE-LOG eyeball.

## Development / testing

```bash
make test        # non-GPU suite (Ollama mocked via FakeLLM) — runs anywhere
make test-gpu    # GPU suite (@pytest.mark.gpu) — run only on the 5070 with Ollama up
make lint        # ruff
make dev         # uvicorn --reload on :8712 (TTS_ENV=dev, echo enabled)
```

All non-GPU tests use `FakeLLMClient` — no network, no model. Real generation is only exercised
behind `-m gpu`, which asserts schema/mechanics, never wording.

**Fixtures.** `tests/fixtures/book/` holds two sets of *The Time Machine* (PG #35) pages: T5's
per-case excerpts `0[1-4]_*.txt` (non-consecutive, chosen per character) and T6's **3 consecutive**
pages `page_[abc].txt` (for `scene-update` threading). Any test globbing `book/*.txt` must scope its
pattern (the T5 cast-mentions GPU test globs `0*.txt`) so the two sets don't collide.

**Options-aware validators.** Most validators take only the parsed output. One that needs the
request options (e.g. `illustration-prompt`'s `depicted ⊆ cast` soft check) opts in with a
`wants_options = True` marker on the callable; the pipeline then calls it `validator(output,
options)`. Reuse the marker rather than widening the `Validator` type for every check.
