# Notes for next cycles

Out-of-scope discoveries parked here during T1 (not implemented — scope fence).

## From T6 — for later cycles
- **The Scriptorium bake surface is COMPLETE.** With scene-update (P3) + illustration-prompt (P5) the
  service now exposes every transform the bake needs (P1 cast-mentions, P2 cast-canonicalize, P3, P5).
  T7 is ops (auth, `GET /v1/transforms`, logging, systemd) — no new transforms are owed to Scriptorium.
- **Soft-validator mechanism now exists (`warn:` → `meta.warnings`).** A validator reason prefixed
  `"warn:"` is recorded to `meta.warnings[]` without failing/​retrying; `meta.warnings` is **omitted when
  empty** (keeps the §4 8-key meta shape — consumers use `meta.get("warnings")`). Warnings come only from
  the successful attempt. To add another soft check, return `"warn:<reason>"` from a validator.
- **Options-aware validators** opt in via a `wants_options = True` marker on the callable; the pipeline
  then calls `validator(output, options)`. Only `depicted_subset_of_cast` uses it. If T7+ needs more
  options-aware checks, reuse the marker rather than widening the `Validator` type for everyone.
- **`book/` fixtures now hold two sets:** T5's per-case excerpts `0[1-4]_*.txt` and T6's **3 consecutive**
  pages `page_[abc].txt` (Ch. I dinner → Ch. II vanishing, stable smoking-room location) + `scene_start.json`
  (page-1 options) + `illustration_cast.json`. Any test that globs `book/*.txt` must scope its pattern
  (the T5 cast-mentions GPU test now globs `0*.txt`).
- **`illustration-prompt` deferred model option:** §7.5 notes `qwen3:14b` as a possible swap if an M1
  blind read shows subject-selection weakness. Not this cycle; the current binding is `qwen3.5:9b`.
- **Observed model behaviour (qwen3.5:9b), kept OUT of assertions:** scene-update may leave `carry_notes`
  empty (`""`, schema-valid); illustration-prompt readily depicts characters beyond the passed `cast`
  (hence the soft warn is expected to fire on real prose). GPU tests assert shape/bounds only.
- **No out-of-scope discoveries** surfaced in T6.

## ✅ RESOLVED in T3 — model blocker + think field
- The absent `qwen3:8b`/`qwen3:0.6b` were **rebound** to `qwen3.5:9b` (default) / `qwen3.5:2b`
  (test/echo) — same weight classes, human-approved. **Production transforms T4–T6 must bind
  `qwen3.5:9b`** (not the DESIGN §2 `qwen3:8b` string). Full record in `docs/models.md`.
- Thinking is disabled via the **top-level `think: false`** request field (verified live on
  Ollama 0.30.7). No `/no_think` tag needed. Transforms set `think=False` (the dataclass default).

## T2/T3 — modules deliberately NOT created in T1
- `llm.py`, `registry.py`, `pipeline.py`, `budget.py` are later-cycle scope and were left
  uncreated on purpose (not even as empty stubs). The `/health` Ollama probe lives in
  `tts/health.py` and is intentionally separate from the future `OllamaClient` (T3).

## From T2 — conventions later cycles must follow

- **Prompt-template convention (T4-T6).** `render_messages` (in `pipeline.py`) renders the
  transform's `template` (Jinja2), splits on the first `USER:` marker into system/user
  messages, strips a leading `SYSTEM:`, and replaces the literal token `{common framing}` with
  the §7 constant. **Write each §7 transform template verbatim in this SYSTEM/USER form** and it
  drops in unchanged. Templates already use `| tojson` / `| join` / `| default` — all Jinja2
  built-ins, no filter registration needed.
- **Validators are top-level-field only (~~add nested for T5~~ ✅ DONE in T5).** `no_empty_strings`
  now also handles the `mentions[].name` array-of-objects path; other factories remain top-level
  (that is all the catalog needs). See the "From T5" section above.
- **T3 wiring seam.** The route returns `503 model_unavailable` while `app.state.llm is None`.
  T3 sets `app.state.llm = OllamaClient(...)` at startup and maps Ollama-level failures to
  `503 model_unavailable` inside/around the client. The single-slot `app.state.gen_semaphore`
  already exists; `queue_wait_s` comes from settings. `FakeLLMClient` supports async callables
  (used by the sleepy queue-timeout test) — reuse that pattern for any T3 timing tests.
- **`meta.input_tokens_est`** is the estimate of the *post-truncation* text actually sent (equals
  the original when not truncated). Keep this if adding fields.

## From T5 — for later cycles
- **Nested-field validators now exist (RESOLVED — was carried from T2/T3).** `no_empty_strings(field)`
  accepts a one-level array-of-objects path `"<array>[].<sub>"` (e.g. `mentions[].name`) as well as the
  top-level list form. It is deliberately one level deep — the only shape the catalog needs. If T6+ needs
  deeper/other nested checks, generalize the same `"[]."` split rather than adding a JSONPath dependency.
- **`book/` fixture set reused/extended by T6.** `tests/fixtures/book/` holds 4 *Time Machine* (PG #35)
  excerpts (555–608 w) + `canonicalize_time_traveller.json`. T6's `scene-update` needs **3 _consecutive_**
  pages — extend this set (the excerpts above are non-consecutive, chosen per-case); commit the new
  consecutive triple alongside a hand-written `prior_ledger: null` start.
- **`over_budget="reject"` → 413 works end-to-end** (cast-mentions; also `scene-update` in T6). The 413
  fires before any LLM call — unit-test it with `fake.calls == []`.
- **Verbatim templates with >100-char lines:** `cast_canonicalize.py`'s `_TEMPLATE` is built from adjacent
  string literals split only at the two long lines (no newline at the join → byte-identical render, verified).
  Reuse this pattern for any T6 template line that trips ruff E501 — it keeps templates verbatim without a
  lint-config change or a `version` bump.
- **Real-model quirks seen on cast-mentions (qwen3.5:9b):** occasional empty descriptor `""` (instead of
  `[]`) and a rare stray non-Latin token mid-descriptor; both schema-valid. GPU tests assert shape only —
  do NOT tighten to wording. The zero-character-page assertion must stay **loose** (first-person prose
  surfaces the lone narrator; assert "no invented cast", not `[]`).
- **No out-of-scope discoveries** surfaced in T5.

## From T4 — for later cycles
- **First production transform (`image-prompt`) needed zero new infrastructure** — it is pure
  composition of the T2 pipeline + validators + budget and the T3 client. T5/T6 transforms should
  be the same *except* T5's nested-field validator (see below). Template shipped verbatim from §7.1.
- **Production transforms register unconditionally** in `register_all` (above the `is_dev` echo
  block). Only `echo` is env-gated. Add T5/T6 transforms the same way.
- **Reusable fixture + GPU pattern (T4):** `tests/fixtures/<domain>/*.txt`; unit tests load via a
  `Path(__file__).parent / "fixtures" / ...` helper and drive the *real* `build_x()` transform with
  `FakeLLMClient`; the GPU test runs all fixtures through `run_transform` on the real binding and
  prints outputs with `capsys.disabled()` for the human-eyeball CYCLE-LOG paste. A no-raise result
  already means schema + validators passed (the pipeline enforces both) — assert shape/`meta` only.
- **No out-of-scope discoveries** surfaced in T4.

## From T3 — for later cycles
- **Constrained decoding runs through `/api/generate`, not `/api/chat`** (this Ollama silently
  ignores `format` on `/api/chat`). T4–T6 transforms don't touch this — they just supply an
  `output_schema` and it is enforced. But if `/api/chat` is ever needed (multi-turn), re-verify
  format enforcement first; the seam is `OllamaClient.chat`. See `docs/models.md`.
- **~~Nested-field validators are still missing~~ ✅ RESOLVED in T5.** `no_empty_strings` now handles
  the `mentions[].name` array-of-objects path (one level deep); unit-tested standalone. See "From T5".
- **GPU-test pattern (reuse in T4–T6):** `tests/test_gpu.py` builds a real `OllamaClient` from
  `Settings.from_env()`, marks with `pytest.mark.gpu`, and asserts schema/mechanics only. Bind
  test transforms to `qwen3.5:2b` for speed; assert the production binding string separately.
- **~~`meta.warnings` soft-validator mechanism (T6) is not built yet~~ ✅ RESOLVED in T6.** The pipeline
  now records a `warn:<reason>` validator finding to `meta.warnings` (omit-when-empty) without failing or
  retrying. See "From T6".

## Housekeeping
- `text-transform-service-design.md` (lowercase) is the superseded v1 draft; it sits untracked
  in the repo root. Left as-is (not created by this cycle). Consider removing it once the v2
  `text-transform-service-DESIGN.md` is confirmed canonical.
- `tts/app.py` resolves `Settings` once at import. Tests override via `app.state.settings`.
  When auth/logging land (T7), consider a lifespan-based settings/state wiring.
- Starlette TestClient emits a `StarletteDeprecationWarning` about httpx; harmless, no action.
