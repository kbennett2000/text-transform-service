# Handoff

## Current state
Cycle **T4 complete** (PR open for human merge). The **first production transform ships**:
- `transforms/image_prompt.py` — `image-prompt` (DESIGN §7.1), bound to `qwen3.5:9b`. News story →
  one concrete, style-free image *subject* prompt. Schema (`prompt`, 30–400 chars) + validators
  (`banned_substrings`, `word_range(8,60)`) + `lede_first_n` truncation at a 3000 est-token budget.
  Registered in **every** environment (production transform; `echo` remains dev-gated).
- `tests/fixtures/news/` — 5 synthetic wire-service stories (one multi-topic, one >3000 est-tokens).
- 81 non-GPU tests pass; 4 GPU tests pass on the 5070. All 5 fixtures produced sane one-line prompts
  on `qwen3.5:9b` (pasted in CYCLE-LOG); the long fixture truncates (`meta.truncated:true`).
- **No new infrastructure** — T4 is pure composition of the T2 pipeline + T3 client. The §7.1
  template shipped verbatim (no refinement needed); `version` `0.1.0`.

## The full T1–T3 base (unchanged this cycle)
- `llm.py` — `OllamaClient` via `POST /api/generate` (`format`-constrained, `think:false`,
  `keep_alive`, 120s); `list_tags`/`list_loaded`/`unload`; `LLMBackendError`.
- `pipeline.py` — full §3 pipeline; `params` carries per-request `model`; `LLMBackendError` → `503
  model_unavailable` (no retry).
- `startup.py` + `app.py` lifespan — non-fatal missing-model warning; `POST /v1/models/unload`.

## Two Ollama findings that still bind (see `docs/models.md`)
1. **Model rebind:** DESIGN §2's `qwen3:8b`/`qwen3:0.6b` are absent → rebound to **`qwen3.5:9b`**
   (default) / **`qwen3.5:2b`** (test/echo), same weight classes. Production transforms T4–T6
   bind `qwen3.5:9b`.
2. **`/api/chat` ignores `format` on this Ollama (0.30.7); `/api/generate` enforces it.** The
   client therefore uses `/api/generate` (human-approved deviation from DESIGN §5) so
   constrained decoding (ADR-0002) actually holds. If a future Ollama fixes `/api/chat`, the
   switch is a localized change inside `OllamaClient.chat`.

## Next up
- **T5** — `cast-mentions` + `cast-canonicalize` (§7.2–7.3), bound to `qwen3.5:9b`. **Needs the
  nested-field validator** extension (`no_empty_strings(mentions[].name)`) — `validators.py` is
  still top-level-field only. Book-page fixtures from *The Time Machine* (PG #35). Reuse the T4
  fixture-loader + GPU-eyeball pattern (`tests/fixtures/`, `run_transform` on all fixtures,
  `capsys.disabled()` print). Note: `cast-mentions` budget is `reject` (page-over-budget → 413),
  unlike image-prompt's `truncate`.
- **T6** — `scene-update` + `illustration-prompt` (+ soft `meta.warnings` validator mechanism).
- **T7** — auth (`X-Transform-Key`), `GET /v1/transforms`, structured logging + `X-Request-Id`,
  systemd unit under `deploy/`.

## Open questions / notes
- Production-transform bindings must use `qwen3.5:9b` (not the DESIGN §2 `qwen3:8b` string).
- All GPU tests assert schema/mechanics only — never wording (the 2b model's echo output is
  rough on purpose; irrelevant).
