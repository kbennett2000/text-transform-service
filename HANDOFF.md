# Handoff

## Current state
Cycle **T2 complete** (PR open for human merge). The full DESIGN §3 request pipeline runs
end-to-end against a fake LLM, exposed via `POST /v1/transform/{name}`:
- `registry.py` (`Transform` dataclass + `register()` + duplicate guard), `llm.py`
  (`LLMClient` protocol + `FakeLLMClient`), `budget.py` (token estimate + `lede_first_n`/`head`),
  `validators.py`, `pipeline.py` (options/budget/render/semaphore/retry, full §4 error taxonomy).
- Dev-only `echo` transform (registered only when `TTS_ENV=dev`) proves the plumbing.
- `/health` unchanged from T1. 55 non-GPU tests pass, ruff clean.

There is **no real generation backend yet**: `app.state.llm = None`, so a live POST returns
`503 model_unavailable`. Tests inject `FakeLLMClient` via the `get_llm_client` dependency.

## Next up
- **T3** — `OllamaClient` implementing `LLMClient` (`/api/chat`, `format`=output_schema,
  `think` field, `keep_alive`), wired to `app.state.llm`; real semaphore/queue behavior;
  `/v1/models/unload`; startup model check; GPU tests. **BLOCKED on models** (see below).
- **T4-T6** — real transforms; their §7 templates drop straight into `Transform.template`
  thanks to the SYSTEM/USER `render_messages` convention established in T2.
- **T7** — auth, `/v1/transforms` listing, logging, systemd.

## Open questions / blocked
- **Models missing (blocks T3+):** `qwen3:8b` and `qwen3:0.6b` are still not installed; box has
  `qwen3.5:*`, `lfm2.5:8b`, `llama3.1:8b`. Human must pull the tags or choose same-weight-class
  replacements per DESIGN §0.1 and update `docs/models.md`. T2 is unaffected (FakeLLM only).
- **T3 must set `app.state.llm`** to the real `OllamaClient` and can then retire the
  `model_unavailable` guard in the route, or keep it for the not-configured case.
