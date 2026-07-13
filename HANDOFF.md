# Handoff

## Current state
Cycle **T3 complete** (PR open for human merge). The service now does **real, schema-constrained
generation** end-to-end:
- `llm.py` — `OllamaClient` (`LLMClient`) via `POST /api/generate` (`format`-constrained,
  `think:false`, `keep_alive`, 120s), plus `list_tags`/`list_loaded`/`unload`; `LLMBackendError`
  for backend failures.
- `pipeline.py` — `params` carries the per-request `model`; `LLMBackendError` → `503
  model_unavailable` (fail-fast, no retry).
- `startup.py` + `app.py` lifespan — loud, non-fatal warning for registry-bound models Ollama
  hasn't pulled. `app.state.llm` is a live `OllamaClient`.
- `POST /v1/models/unload` — `{"model"}` or `{}`; confirms via `/api/ps` (bounded poll).
- `echo` rebound to `qwen3.5:2b`. 75 non-GPU tests pass; 3 GPU tests pass on the 5070.

## Two Ollama findings that shaped T3 (see `docs/models.md`)
1. **Model rebind:** DESIGN §2's `qwen3:8b`/`qwen3:0.6b` are absent → rebound to **`qwen3.5:9b`**
   (default) / **`qwen3.5:2b`** (test/echo), same weight classes. Production transforms T4–T6
   bind `qwen3.5:9b`.
2. **`/api/chat` ignores `format` on this Ollama (0.30.7); `/api/generate` enforces it.** The
   client therefore uses `/api/generate` (human-approved deviation from DESIGN §5) so
   constrained decoding (ADR-0002) actually holds. If a future Ollama fixes `/api/chat`, the
   switch is a localized change inside `OllamaClient.chat`.

## Next up
- **T4** — `image-prompt` transform (§7.1), bound to `qwen3.5:9b`. Its SYSTEM/USER §7 template
  drops straight into `Transform.template` (the T2 `render_messages` convention). Fixtures +
  unit tests (FakeLLM) + a GPU test.
- **T5** — `cast-mentions` + `cast-canonicalize`; **needs the nested-field validator** extension
  (`no_empty_strings(mentions[].name)`) — validators are still top-level-field only.
- **T6** — `scene-update` + `illustration-prompt` (+ soft `meta.warnings` validator mechanism).
- **T7** — auth (`X-Transform-Key`), `GET /v1/transforms`, structured logging + `X-Request-Id`,
  systemd unit under `deploy/`.

## Open questions / notes
- Production-transform bindings must use `qwen3.5:9b` (not the DESIGN §2 `qwen3:8b` string).
- All GPU tests assert schema/mechanics only — never wording (the 2b model's echo output is
  rough on purpose; irrelevant).
