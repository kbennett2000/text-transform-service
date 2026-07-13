# Handoff

## Current state
Cycle **T5 complete** (PR open for human merge). The **two Scriptorium cast transforms ship**:
- `transforms/cast_mentions.py` — `cast-mentions` (DESIGN §7.2), `qwen3.5:9b`. Per-page character
  extraction → mentions array (`name`/`aliases`/`descriptors`/`is_person`). Budget 1600 est-tokens
  with **`over_budget="reject"`** (page over budget → 413; paginator-bug posture). Validator
  `no_empty_strings("mentions[].name")`.
- `transforms/cast_canonicalize.py` — `cast-canonicalize` (DESIGN §7.3), `qwen3.5:9b`. Per-character
  evidence (rides in `options`; `text` empty) → paintable canonical description (`visual_description`
  80–700, `one_line` 15–160, `tags` ≤8). Validator bans trait-drift words in `visual_description`.
- `validators.py` — **nested-field validator now exists**: `no_empty_strings` handles the
  `mentions[].name` array-of-objects path (one level). Resolves the T2/T3 carried blocker.
- Both registered in **every** environment (production; `echo` stays dev-gated).
- `tests/fixtures/book/` — 4 *Time Machine* (PG #35) excerpts (555–608 w, the four §-cases) +
  `canonicalize_time_traveller.json` options payload.
- 91 non-GPU tests pass; 6 GPU tests pass on the 5070. Both §7.2/§7.3 templates shipped **verbatim**
  (no refinement; both `version` `0.1.0`). GPU outputs pasted in CYCLE-LOG.

## Earlier: T4 — `image-prompt` (DESIGN §7.1)
- `transforms/image_prompt.py`, `qwen3.5:9b`. News story → one style-free image *subject* prompt.
  `lede_first_n` truncation at a 3000 est-token budget. `tests/fixtures/news/` (5 synthetic stories).
  Pure composition of the T2 pipeline + T3 client.

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
- **T6** — `scene-update` + `illustration-prompt` (§7.4–7.5), bound to `qwen3.5:9b`. Adds the soft
  **`meta.warnings` validator mechanism** (validators may return `warn:<reason>` → lands in
  `meta.warnings[]` without failing) — a small pipeline extension, unit-test it. `illustration-prompt`
  uses it for the `depicted ⊆ cast names` check (warn, not 422). `scene-update` budget is `reject`
  (like cast-mentions) and threads a ledger **strictly in order** (each call's `prior_ledger` = the
  previous output). Fixtures: **3 _consecutive_** *Time Machine* pages (extend `tests/fixtures/book/`,
  which currently holds non-consecutive per-case excerpts) + a hand-written `prior_ledger: null` start.
  Reuse the nested validator, the `book/` loader, and the GPU-eyeball pattern.
- **T7** — auth (`X-Transform-Key`), `GET /v1/transforms`, structured logging + `X-Request-Id`,
  systemd unit under `deploy/`.

## Open questions / notes
- Production-transform bindings must use `qwen3.5:9b` (not the DESIGN §2 `qwen3:8b` string).
- All GPU tests assert schema/mechanics only — never wording (the 2b model's echo output is
  rough on purpose; irrelevant).
