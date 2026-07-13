# Cycle Log

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
