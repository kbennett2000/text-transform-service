# Handoff

## Current state
Cycle **T6 complete** (PR open for human merge). With T6 the service exposes **every Scriptorium bake
transform** (P1 cast-mentions, P2 cast-canonicalize, P3 scene-update, P5 illustration-prompt). The **two
final Scriptorium transforms ship**, plus the soft-validator mechanism:
- `transforms/scene_update.py` — `scene-update` (DESIGN §7.4), `qwen3.5:9b`. Per-page rolling ledger +
  salience. Called **strictly in order**; caller threads each returned ledger into the next call's
  `prior_ledger` (object-or-null). 8-field ledger output; budget 1600 **`over_budget="reject"`**;
  validator `banned_substrings("best_visual_beat", ["\n"])`.
- `transforms/illustration_prompt.py` — `illustration-prompt` (DESIGN §7.5), `qwen3.5:9b`. (page, ledger,
  cast) → one SDXL subject prompt (`prompt` 60–600, `depicted` ≤4, `shot` enum). Hard validators
  `word_range(20,90)` + `banned_substrings` (medium words); **soft** `depicted_subset_of_cast()`.
- `pipeline.py` — **soft-validator mechanism**: a `warn:<reason>` validator finding lands in
  `meta.warnings[]` without failing/retrying; `meta.warnings` is **omitted when empty** (§4 meta shape
  unchanged). Warnings taken only from the successful attempt.
- `validators.py` — `depicted_subset_of_cast()`, an **options-aware** validator (opts in via a
  `wants_options` marker → pipeline calls it `validator(output, options)`). Existing validators untouched.
- Both transforms registered in **every** environment (production; `echo` stays dev-gated).
- `tests/fixtures/book/` — **3 consecutive** *Time Machine* pages `page_[abc].txt` (Ch. I dinner → Ch. II
  vanishing; stable smoking-room location) + `scene_start.json` + `illustration_cast.json`, alongside T5's
  `0[1-4]_*.txt` excerpts.
- 105 non-GPU tests pass; 7 GPU tests pass on the 5070. Both §7.4/§7.5 templates shipped **verbatim** (both
  `version` `0.1.0`). GPU outputs (3 threaded ledgers + the illustration prompt, with the soft warn firing
  live) pasted in CYCLE-LOG.

## Earlier: T5 — the cast transforms (DESIGN §7.2–7.3)
- `transforms/cast_mentions.py` (`qwen3.5:9b`, budget 1600 `reject`, validator
  `no_empty_strings("mentions[].name")`) and `transforms/cast_canonicalize.py` (`qwen3.5:9b`, evidence in
  `options`). `validators.py` gained the nested `mentions[].name` path. Fixtures: 4 per-case PG #35
  excerpts + `canonicalize_time_traveller.json`.

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
- **T7 (ops hardening)** — auth (`X-Transform-Key` dependency, active only when `TRANSFORM_API_KEY` set;
  `/health` exempt; 401 in the §4 envelope), `GET /v1/transforms` (serialize the registry incl. schemas),
  structured logging (one JSON line/request per DESIGN §9) + `X-Request-Id`, and a
  `deploy/text-transform-service.service` systemd unit + `deploy/README.md`. No new transforms — the
  Scriptorium bake surface is complete. See BUILD-PLAN §Cycle T7.

## Open questions / notes
- Production-transform bindings must use `qwen3.5:9b` (not the DESIGN §2 `qwen3:8b` string).
- All GPU tests assert schema/mechanics only — never wording (the 2b model's echo output is
  rough on purpose; irrelevant).
