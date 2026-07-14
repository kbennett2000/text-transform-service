# AI reference — text-transform-service

> Dense, single-load context for an AI agent. Human docs: [README](../README.md) ·
> [for-developers.md](for-developers.md). Authoritative detail:
> [DESIGN](../text-transform-service-DESIGN.md), [models.md](models.md).

**Identity.** Self-hosted FastAPI service. Named transforms map `text (+options) → schema-constrained
JSON` via a local LLM (Ollama, `/api/generate`, constrained decoding). LAN-only, keyless by default,
single-GPU (RTX 5070), port **8712**. **Not** a general LLM gateway. Consumers: Brickfeed News
(`image-prompt`, `story-cover`, `opinion-gate`, `opinion-image-brief`) and Scriptorium bakery
(`cast-*`, `scene-update`, `illustration-prompt`).

**Invariants (violating any is a bug).**
- Error codes (below) are a **frozen contract** with two consumers — changing a code is breaking.
- `/health` **never 500s**; Ollama down → `200` with `status:"degraded"`, `ollama_reachable:false`.
- **One in-flight generation**, serialized by a single-slot semaphore; overflow queues, then `503 busy`
  after `QUEUE_WAIT_S`.
- Output is **schema-constrained** (`output_schema` → Ollama `format` grammar) **and** re-validated
  post-generation; validator failure → temp-bumped retry → `422`.
- Model thinking disabled (`think:false`). Never substitute model tags (bindings in [models.md](models.md)).
- Auth is per-route on `/v1/*` only; `/health` is always open.

## Endpoints

| Method + path | Auth | Purpose | Notes |
|---|---|---|---|
| `GET /health` | never | service + Ollama status | never 500s; `{status, ollama_reachable, models_loaded[], uptime_s}` |
| `GET /v1/transforms` | when enabled | registry listing | each: `name, version, model, input_budget, over_budget, options_schema, output_schema`; template + validators never exposed |
| `POST /v1/transform/{name}` | when enabled | run transform | body `{text, options?}`; `200 {output, meta}` |
| `POST /v1/models/unload` | when enabled | free VRAM | body `{model}` or `{}` (all); `→ {unloaded:[...]}` (confirmed via `/api/ps`) |

**Auth (ADR-0003):** enabled only when `TRANSFORM_API_KEY` set → every `/v1/*` needs header
`X-Transform-Key: <key>`. Unset → keyless. Every response carries `X-Request-Id` (uuid4 hex short).

**Success envelope:** `{"output": {...schema...}, "meta": {...}}`.
`meta` = `transform, transform_version, model, input_tokens_est, truncated, attempts, latency_ms,
queued_ms` (+ `warnings:[...]` **only when** a soft validator fired; absent on clean path — read via
`meta.get("warnings")`).

**Error envelope:** `{"error": {"code", "message", "detail?}}`.

## Error taxonomy (frozen — DESIGN §4)

| HTTP | code | Meaning |
|---|---|---|
| 400 | `bad_request` / `bad_options` | malformed body / `options` fails `options_schema` |
| 401 | `unauthorized` | auth enabled, key missing/wrong |
| 404 | `unknown_transform` | no such transform name |
| 413 | `over_budget` | input over budget **and** transform is `over_budget:"reject"` (fires before any LLM call) |
| 422 | `validation_failed` | generation failed validators after retries |
| 503 | `busy` / `model_unavailable` | queue timeout / Ollama-level failure (no retry) |
| 500 | `internal` | unexpected bug |

## Transforms

| name | consumer / §  | model | budget | over-budget | `text` | `options` (shape) | `output` keys |
|---|---|---|---|---|---|---|---|
| `image-prompt` | Brickfeed §7.1 | `qwen3.5:9b` | 3000 | **truncate** (`lede_first_n`, paragraph-boundary, `meta.truncated`) | news story | `{}` | `prompt` (8–60w, 1 line, no style/medium/camera words) |
| `cast-mentions` | Scriptorium P1 §7.2 | `qwen3.5:9b` | 1600 | **reject** → 413 | one book page | `{}` | `mentions[]:{name, aliases, descriptors(verbatim), is_person}` |
| `cast-canonicalize` | Scriptorium P2 §7.3 | `qwen3.5:9b` | 1200 | truncate | `""` (empty) | `{name, descriptors, aliases?, era?, genre?}` (name+descriptors required) | `visual_description, one_line, tags` |
| `scene-update` | Scriptorium P3 §7.4 | `qwen3.5:9b` | 1600 | **reject** → 413 | one page (call **in order**) | `{prior_ledger: obj\|null, cast_names[], era?}` (thread each output ledger → next `prior_ledger`) | `location, time_of_day, atmosphere, present, scene_changed, visual_salience, best_visual_beat, carry_notes` |
| `illustration-prompt` | Scriptorium P5 §7.5 | `qwen3.5:9b` | 1600 | **reject** → 413 | selected page | `{ledger: obj, cast:[{name, one_line}], era?}` | `prompt, depicted, shot(enum), avoid?` — `depicted⊆cast` is a **soft** validator → `meta.warnings` |
| `story-cover` | Brickfeed (request §1, T9) | `qwen3.5:9b` | 1200 | truncate (no-op on single-paragraph input; never rejects) | source context (title/publisher/URL) | `{}` | `headline(10–200), description(40–600), imagePrompt(30–400, 8–60w, subject-only), category(enum×8), caption(15–160)` |
| `opinion-gate` | Brickfeed (request §2, T10) | `qwen3.5:9b` | 1600 | **reject** → 413 | JSON array `[{id,title,summary}]` | `{}` | `verdicts[]:{id, verdict(enum: eligible/excluded/uncertain), reason(1–200)}` (maxItems 100) — **safety classifier under ADR-0007; caller fail-closes** |
| `opinion-image-brief` | Brickfeed (request §4, T10) | `qwen3.5:9b` | 3000 | truncate (`head`) | finished piece (title+body) + subject context | `{}` | `imagePrompt(30–400, 8–60w, subject-only), caption(15–160)` — depicts the subject, never the author/act-of-writing |
| `echo` | dev-only (`TTS_ENV=dev`) | `qwen3.5:2b` | — | — | any | `{}` | `echo` (first sentence) — plumbing check, not a workload |

Style/medium/camera words are always caller-side; their appearance in an `image-prompt` /
`illustration-prompt` / `story-cover` `imagePrompt` output is drift → `422`. Brickfeed's toy-brick
styling is applied caller-side, never in a transform (ADR-0004).

**`opinion-gate` is a safety classifier (ADR-0007).** It is admitted to the charter *conditionally*
(§1 otherwise excludes safety-relevant classification). It is **fail-loud** — it emits an honest
`uncertain` verdict and never substitutes a default. The **caller must fail-closed**: treat every
4xx/5xx, every `uncertain` verdict, and any missing/duplicate `id` as *exclude*. `verdict` is the
sole decision field; `reason` is explanatory only. See `docs/adr/0007-safety-classification-exception.md`
and `docs/requests/brickfeed-2026-07-RESPONSE.md`.

**Held (not registered):** `opinion-piece` (Brickfeed request §3) — long-form *voiced* generation,
which DESIGN §1 excludes; ADR-0007 amended only the safety-classification line, not this one. Not
built; the task stays on Brickfeed's incumbent provider pending a bench + product decision.

## Config (env, all optional)

| Var | Default | Meaning |
|---|---|---|
| `TTS_PORT` | `8712` | bind port |
| `TTS_HOST` | `0.0.0.0` | LAN bind |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama runtime |
| `OLLAMA_KEEP_ALIVE` | `5m` | passed on every generate |
| `TRANSFORM_API_KEY` | unset | enables shared-secret auth when set |
| `QUEUE_WAIT_S` | `90` | generation queue timeout → `503 busy` |
| `TTS_LOG_LEVEL` | `INFO` | log level |
| `TTS_ENV` | `prod` | `dev` registers dev-only transforms (`echo`) |

The systemd unit hardcodes `--host 0.0.0.0 --port 8712` in `ExecStart`; it does **not** read
`TTS_HOST`/`TTS_PORT`.

## Model bindings (docs/models.md)

- Production default: **`qwen3.5:9b`**. Test/CI + `echo`: **`qwen3.5:2b`**.
- Rebound in T3 from absent DESIGN §2 tags (`qwen3:8b`/`qwen3:0.6b`) — same weight classes.
- Ollama 0.30.7: `/api/chat` **ignores** `format`; client uses `/api/generate` (enforces it). Constrained
  decoding depends on this.

## Logging (DESIGN §9)

One JSON line per `/v1/*` request on logger `tts.request` (`/health` excluded):
`ts, request_id, transform, status` + `attempts, input_tokens_est, truncated, queued_ms, latency_ms` on a
completed run, `error_code` on failure. Under systemd → `journalctl -u text-transform-service`.

## File map

| Path | Role |
|---|---|
| [`../src/tts/app.py`](../src/tts/app.py) | routes, auth dependency (`require_api_key`), `log_requests` middleware, `TransformError` handler, lifespan |
| [`../src/tts/pipeline.py`](../src/tts/pipeline.py) | request pipeline: budget → render template → generate → validate/retry → soft-warnings |
| [`../src/tts/llm.py`](../src/tts/llm.py) | `LLMClient` / `OllamaClient` (`/api/generate`) / `FakeLLMClient`; `list_tags`/`list_loaded`/`unload` |
| [`../src/tts/budget.py`](../src/tts/budget.py) | token estimate + truncation (`lede_first_n`) |
| [`../src/tts/registry.py`](../src/tts/registry.py) | frozen `Transform` dataclass + registry |
| [`../src/tts/validators.py`](../src/tts/validators.py) | validator factories (`word_range`, `banned_substrings`, `no_empty_strings`, `depicted_subset_of_cast`) |
| [`../src/tts/transforms/`](../src/tts/transforms/) | one module per transform, `build_<name>() -> Transform` |
| [`../src/tts/logging_setup.py`](../src/tts/logging_setup.py) | idempotent `configure_logging`; `tts.request` pure-JSON, `propagate=False` |
| [`../deploy/`](../deploy/) | systemd unit + install README |

Tests: non-GPU (`FakeLLMClient`, runs anywhere) vs `@pytest.mark.gpu` (real binding on the 5070; asserts
schema/shape/bounds only, **never wording**). `make test` / `make test-gpu` / `make lint` / `make dev`.
