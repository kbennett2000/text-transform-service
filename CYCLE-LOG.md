# Cycle Log

## T2 — Registry, pipeline, FakeLLM, `echo` transform (2026-07-13)

**Shipped**
- `registry.py` — `Transform` frozen dataclass field-for-field per DESIGN §6; `Validator`
  type; `REGISTRY` + `register()` raising `ValueError` on duplicate names (startup error).
- `llm.py` — `LLMClient` protocol (`async chat(messages, format_schema, params) -> str`) and
  `FakeLLMClient` (list-or-callable responses, records every call incl. params/schema). No
  `OllamaClient` (T3).
- `budget.py` — `estimate_tokens` = `ceil(words × 1.35)`; `lede_first_n`/`head` truncation on
  blank-line paragraph boundaries, both `(text, truncated)`; single-paragraph input untouched.
- `validators.py` — `max_chars`, `min_chars`, `banned_substrings`, `no_empty_strings`,
  `word_range` (top-level fields; nested paths deferred to T5).
- `pipeline.py` — full §3 pipeline: options→schema (400 `bad_options`), budget (413
  `over_budget` / truncate+`meta.truncated`), `render_messages` (SYSTEM/USER split +
  `{common framing}`), semaphore-serialized generation (503 `busy` on queue timeout),
  parse+schema+validators with retry & temp-bump (422 `validation_failed`,
  `detail.reasons` len = retries+1), full `meta` block. `TransformError` → §4 taxonomy.
- `config.py` — added `TTS_ENV` / `is_dev` (echo dev gate).
- `transforms/echo.py` + `transforms/__init__.py::register_all(settings)` — dev-only `echo`
  (bound to `qwen3:0.6b`, never called under FakeLLM), registered only when `TTS_ENV=dev`.
- `app.py` — `POST /v1/transform/{name}` wired to the pipeline with a `get_llm_client`
  dependency (FakeLLM via override in tests; `app.state.llm=None` in T2 → 503
  `model_unavailable` live); `RequestValidationError` → 400 `bad_request`; unexpected → 500
  `internal`; single-slot `gen_semaphore` on app state.
- Tests: registry (defaults/frozen/duplicate), budget (estimate + both strategies +
  no-blank-lines), validators (each), pipeline (bad_options/over_budget-reject/truncate/
  retry-temp-bump/always-invalid-422/validator-retry/503-busy×2/full-meta/render_messages),
  route (404/200+meta/omitted-options/400/500/dev-gate). Makefile `dev` sets `TTS_ENV=dev`;
  README documents the transform endpoint, error taxonomy, and `TTS_ENV`.

**Verification**
- `make lint` clean; `make test` → 55 passed. Every §4 code (400 bad_request, 400
  bad_options, 404, 413, 422, 503 busy, 500) has a test; 503 busy proven via a pre-acquired
  semaphore *and* a concurrent sleepy-FakeLLM race.
- Live boot (`TTS_ENV=dev`): `/health` ok; `POST /v1/transform/echo` → 503 `model_unavailable`
  (no backend until T3); unknown name → 404.

**Deviations / decisions**
- **`template` → messages convention.** §6's dataclass has only `template: str` and every §7
  template is written `SYSTEM: {common framing} … USER: …`. `render_messages` renders the
  Jinja2, splits on the first `USER:` marker, strips `SYSTEM:`, and substitutes
  `{common framing}` with the §7 constant — so T4-T6 templates drop in verbatim.
- **`TTS_ENV`** added (not in §9's table) purely to gate the dev-only `echo`; documented.
- **`register_all(settings)`** performs the explicit-list registration at startup (refines
  §6's import-side-effect) to enable the env gate + test isolation.
- **Validators are top-level-field**; nested-array paths (`mentions[].name`) land in T5.
- **No `OllamaClient`; `app.state.llm=None`** — real generation + `model_unavailable`/real
  `busy` wiring is T3. (Models still absent on the box; unchanged blocker for T3+.)

## T1 — Scaffold, ADRs, /health (2026-07-13)

**Shipped**
- uv project scaffold: `pyproject.toml` (Python 3.12; deps fastapi, uvicorn[standard],
  httpx, jinja2, pydantic v2, jsonschema; dev pytest, pytest-asyncio, ruff, respx), `uv.lock`,
  src layout `src/tts/`, empty `src/tts/transforms/` package.
- `tts/config.py` — `Settings` reading the DESIGN §9 env table with defaults; `auth_enabled`
  derived from `TRANSFORM_API_KEY`.
- `tts/health.py` — minimal async Ollama probe (`/api/ps` + `/api/tags`, 3s timeout), never
  raises; reachability tied to `/api/ps` per DESIGN §4.
- `tts/app.py` — FastAPI app + `GET /health` (`ok`/`degraded`, `ollama_reachable`,
  `models_loaded`, `uptime_s`); never 500s.
- ADRs: `docs/adr/0000-template.md`; `0001-stack`, `0002-runtime-ollama`, `0003-auth`,
  `0004-style-wrapping-caller-side`, `0005-concurrency` — transcribed verbatim from DESIGN §2.
- `docs/models.md` — verbatim `ollama list` + blocker flag (see deviations).
- `Makefile` (`dev`/`test`/`test-gpu`/`lint`/`sync`), `.gitignore` (Python/uv), README stub.
- Tests: `tests/test_config.py` (defaults + per-var overrides + auth toggle + blank handling),
  `tests/test_health.py` (respx: reachable→ok, unreachable→degraded, 5xx→degraded, tags-fail→ok).

**Verification**
- `uv run ruff check .` clean; `uv run pytest -m "not gpu"` → 16 passed.
- Live `/health` with Ollama up → `200 {status:"ok", ollama_reachable:true}`.
- Live `/health` with `OLLAMA_URL` on a dead port → `200 {status:"degraded", ollama_reachable:false}`
  (does not 500).

**Deviations / decisions**
- **Task runner: Makefile (not justfile)** — `just` is not installed on the box; `make` is.
  BUILD-PLAN allows "Makefile or justfile"; acceptance permits `make dev`.
- **ADR numbering collision resolved** — pre-existing `docs/adr/0001-initial.md` (empty
  placeholder) deleted; `docs/adr/0001-cycle-model.md` renumbered to
  `0006-cycle-execution-model.md` (content unchanged) so 0001–0005 could hold the DESIGN §2
  decisions as required by acceptance. (User-approved in plan mode.)
- **BLOCKER — required models absent (does not block T1 code):** neither `qwen3:8b` nor
  `qwen3:0.6b` is installed on the box (`ollama list` shows `qwen3.5:2b/4b/9b`, `lfm2.5:8b`,
  `llama3.1:8b`). Per the hard rule, no substitute was chosen. T1 ships because none of its
  code binds a model. **T3+ are blocked** until a human pulls the tags or picks same-weight-class
  replacements per DESIGN §0.1. Recorded in `docs/models.md` and `NOTES-FOR-NEXT-CYCLES.md`.
