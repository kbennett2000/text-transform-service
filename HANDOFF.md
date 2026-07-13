# Handoff

## Current state
Cycle **T1 complete** (PR open for human merge). Runnable FastAPI service with `GET /health`
(never 500s; `ok`/`degraded` on Ollama reachability), config module (DESIGN §9 env table),
ADRs 0000–0005 (+ renumbered 0006 cycle-execution-model), `docs/models.md`, Makefile, README
stub, and the non-GPU test suite (16 passing, ruff clean).

Not built (later cycles): transforms, registry, pipeline, Ollama generation client, auth,
`/v1/*` routes. See DESIGN §6 for the target layout.

## Next up
- **T2** — registry + pipeline + FakeLLM + `echo` transform. Needs T1 only; **not** blocked by
  the missing models (uses FakeLLM). Safe to dispatch.
- **T3+** — real Ollama generation. **BLOCKED** until the bound models are resolved.

## Open questions / blocked
- **Models missing (blocks T3+):** `qwen3:8b` and `qwen3:0.6b` are not installed; box has
  `qwen3.5:*`, `lfm2.5:8b`, `llama3.1:8b`. Human must pull the tags or choose same-weight-class
  replacements per DESIGN §0.1 and update `docs/models.md`. No substitute was chosen by the
  executor. Full detail in `docs/models.md` and `NOTES-FOR-NEXT-CYCLES.md`.
