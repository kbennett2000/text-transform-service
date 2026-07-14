# Notes for next cycles

Out-of-scope discoveries parked here during T1 (not implemented — scope fence).

## From T13 — Ollama host bindings that large-context transforms depend on
- **q8_0 KV cache is a HOST binding, required for large `opinion-gate` batches.** T12's computed
  `num_ctx` (14144) was necessary but not sufficient: at that context, `qwen3.5:9b` with the
  default **f16** KV cache under flash attention *silently drops the tail* of the verdict array
  (e.g. 27/34, `truncated=0`, no error) — worse than a loud 422, because the caller fail-closed
  rule then over-excludes the tail. Fixed by `OLLAMA_KV_CACHE_TYPE=q8_0` (+ `OLLAMA_FLASH_ATTENTION=1`,
  which q8_0 requires) on the `ollama.service` unit — see `deploy/ollama.service.d/flash-attn.conf`,
  `docs/models.md`, `deploy/README.md` §3a. **Any future transform with a large `input_budget`
  (i.e. a big computed `num_ctx`) inherits this dependency** — verify id-completeness at volume on
  real hardware, don't trust a passing FakeLLM suite.
- **Per-request `flash_attn` / KV-cache-type is IGNORED by Ollama 0.30.7.** The runner reads these
  from daemon-level env once per model load; `options.flash_attn` on `/api/generate` is dropped.
  So attention/KV knobs cannot live in a `Transform` or `OllamaClient` — they are host config. If a
  future Ollama exposes per-request control, the q8_0 binding could move into TTS.
- **The tail-drop was the f16 KV cache, NOT flash attention.** An earlier T13 hypothesis blamed
  flash attention (flash-off *was* complete). But flash-off CPU-offloads at 14336 ctx on the 12 GB
  card (the non-flash attention compute buffer doesn't fit → 74% CPU → ~500 s), and flash-off +
  q8_0 segfaults (`V cache quantization requires flash_attn`). The real fix keeps flash **on** and
  quantizes the KV cache. Don't re-litigate flash-off; it's a dead end on this hardware.
- **Per-story `opinion-gate` calls were REJECTED (product owner, T13).** Latency × volume is
  unacceptable for a cron gate. Batch classification stays the contract.

## From T10 — Brickfeed request set closed out
- **`opinion-gate` shipped under ADR-0007.** The product owner amended §1's blanket
  "no safety-relevant classification" into a *conditional* exclusion
  (`docs/adr/0007-safety-classification-exception.md`). Any **future** safety-relevant
  classifier must satisfy all three ADR-0007 conditions or be HELD: (1) closed enum verdict
  incl. an explicit `uncertain` (no free text drives the decision); (2) module documents the
  caller fail-closed obligation, TTS itself stays fail-loud (no fallback); (3) scope = editorial
  gating of machine-selected public content with human audit — NOT user-generated moderation.
  Reuse `opinion-gate`'s shape (three-value enum, `over_budget=reject`, nested
  `no_empty_strings` on the id + reason) as the template.
- **`opinion-piece` remains HELD (§1 long-form voiced generation).** ADR-0007 amended only the
  *safety-classification* line, not the *long-form-voiced* line, and the product owner did not
  authorize voiced generation. It is **not built** and stays on Brickfeed's incumbent provider.
  Revisiting needs a bench (does the local model voice acceptably?) **and** an explicit product
  decision / new ADR — never an executor call. This closes the T9 ⚠️ warning below.
- **The Brickfeed request set is fully dispositioned.** `docs/requests/brickfeed-2026-07-RESPONSE.md`
  is the contract the Brickfeed *provider* cycle reads: tasks 1 (`story-cover`), 2
  (`opinion-gate`), 4 (`opinion-image-brief`) routable; task 3 (`opinion-piece`) held. **The
  opinion-gate caller fail-closed contract lives there + in the module docstring** — the caller
  must map every 4xx/5xx, every `uncertain` verdict, and any missing/duplicate id to *exclude*.
  After T10, TTS owes Brickfeed nothing further.
- **`opinion-image-brief` reused T9's subject-neutral set verbatim** (`banned_substrings` +
  `word_range("imagePrompt", 8, 60)`), plus a template rule to depict the *subject*, not the
  author or the act of writing. Confirmed on the 5070: no style/medium leak, no writers/desks.
- **New fixture domains:** `tests/fixtures/opinion_gate/*.txt` (JSON-array batches, incl. a
  designed all-tragedy and a borderline case) and `tests/fixtures/opinion_image_brief/*.txt`
  (synthetic finished-piece inputs — bodies are hand-written stand-ins, since `opinion-piece`
  is not built). The T10 GPU test globs each `*.txt`.
- **id-completeness is not schema-enforceable** for batch transforms — validators see output +
  options, never the input `text`. `opinion-gate` relies on the caller fail-closed rule (missing
  id → exclude); the GPU test checks id-set equality as a mechanics guard. Any future batch
  transform with a per-input-id output contract inherits this gap.
- **✅ T5 `cast_canonicalize` GPU flake fixed (authorized in T10).** The `2 ≤ n ≤ 4`
  sentence-count assertion is loosened to `≥ 1` — it was exactly the "never assert
  shape-of-prose" hazard. Closes the T9 flake note below.

## From T9 — for T10 (Brickfeed opinion pair) and the product owner
- **✅ RESOLVED in T10 (ADR-0007).** ~~`opinion-gate` is HELD out of the §1 charter — needs a
  product-owner call, not a build.~~ Shipped in T10 under ADR-0007 (see the From T10 section).
  Original context retained below for the record.
- **`opinion-gate` is HELD out of the §1 charter — needs a product-owner call, not a build.**
  The Brickfeed request (`docs/requests/brickfeed-2026-07.md` §2) is a **fail-closed,
  safety-load-bearing** topic gate (exclude tragedy/violence/death; if uncertain, exclude). That
  is "safety-relevant classification", which DESIGN §1 line 9 + system-overview §5 exclude.
  Building it on TTS would amend the charter → requires a new/updated ADR signed by the product
  owner (Browser Claude / Kris), **not** an executor decision. Until then the incumbent Claude
  gate stays live. If it is ever approved: bound the `verdicts` array (`maxItems`) and the inner
  `reason` (add a `minLength`), keep `over_budget=reject`→413 (the request's own choice — never
  silently drop candidates), and let the *caller* implement fail-closed by treating any 4xx/5xx
  as "all excluded" (TTS itself only fails loudly per §4; it must not swallow errors into a
  default verdict).
- **⚠️ T10 warning — `opinion-piece` likely trips the SAME charter line, differently.** Its
  requested `body` is `minLength 200` with a `[minWords, maxWords]` range up to **2000 words**
  in a **persona's satirical voice** — i.e. long-form *voiced* generation, which DESIGN §1
  ("not for long-form voiced generation") excludes just as explicitly as safety classification.
  **Flag it in T10 plan mode before building**; it may be a second HELD/escalate, not a build.
  `opinion-image-brief` (the other half of T10) is fine and in-charter.
- **Reuse T9's subject-neutral set for `opinion-image-brief`.** Its `imagePrompt` (30–400) and
  `caption` (15–160) bounds are **identical** to `story-cover`'s. Reuse the same validators —
  `banned_substrings(field, ["**","##","http","\n"])` on imagePrompt+caption plus
  `word_range("imagePrompt", 8, 60)` — and the same "no style/medium/toy-brick words" template
  rules (ADR-0004). Do **not** reuse the existing `image-prompt` transform (the request forbids
  it; different schema).
- **`story-cover` truncation is a structural no-op.** `over_budget=truncate`/`head` only cut on
  blank-line paragraph boundaries (`budget.py` `_keep_head` returns unchanged for a
  single-paragraph input). The story-cover input (title/publisher/URL on consecutive lines) is
  one paragraph, so an over-budget input passes through whole (`truncated=False`) and is never
  rejected. Acceptable here (long titles are harmless), but any future single-paragraph
  transform that actually *needs* to shed tokens must not rely on `head`/`lede_first_n`.
- **New fixture domain `tests/fixtures/story_cover/`** — 5 synthetic 3-line `.txt` inputs. The
  T9 GPU test globs `story_cover/*.txt` (scoped, like the T5/T6 `book/` globs).
- **Pre-existing GPU flake to be aware of:** `test_cast_canonicalize_fixture_schema_valid_and_printed`
  (T5) asserts the model returns **2–4 sentences**; `qwen3.5:9b` occasionally emits a single
  (comma-spliced) sentence and the test fails, then passes on re-run. This is exactly the
  "never assert wording/shape-of-prose" hazard — a future cycle that owns T5 could loosen it to
  `>=1 sentence` or drop the count, but it is **out of T9's scope fence** (untouched here).

## From T7 — for later cycles / the human deploy
- **TTS is feature-complete for M1, pending the human systemd deploy.** All transforms + all ops pieces
  (listing, auth, logging, systemd unit) shipped. The one open acceptance box is the human running the
  install on the 5070 (`deploy/README.md`): rsync → `uv sync` → optional `.env` → `systemctl enable
  --now` → confirm `/health` ok and survives reboot. **T8 (Brickfeed bench) stays DEFERRED** — build only
  when explicitly dispatched.
- **§9 systemd-vs-config tension (documented, not "fixed").** The committed unit's `ExecStart` hardcodes
  `--host 0.0.0.0 --port 8712` and does **not** read `TTS_HOST`/`TTS_PORT`; those env vars only affect
  `make dev`. Left verbatim by design (DESIGN §9). If a future cycle wants env-driven bind under systemd,
  switch `ExecStart` to a wrapper that reads the env (e.g. `sh -c 'exec .venv/bin/uvicorn … --port
  ${TTS_PORT:-8712}'`) and bump the deploy docs — but that's a deliberate deviation, not a silent change.
- **Auth is a single dependency (`require_api_key` in `app.py`), attached per-route** via
  `dependencies=[Depends(require_api_key)]` on the three `/v1/*` routes — **not** a global app-level
  dependency (that would gate `/health`). Any new `/v1/*` route must add the dependency explicitly, or it
  ships unauthenticated. `/health` stays deliberately bare.
- **`TransformError` now has a global exception handler** (`_on_transform_error`) for dependency-raised
  errors, **and** the transform route keeps its inline `try/except`. Both are intentional: the inline
  catch orders `except TransformError` before `except Exception` so a real bug still maps to 500. Don't
  remove the inline catch expecting the global handler to cover it — the broad `except Exception` would
  otherwise swallow `TransformError` into a 500.
- **Structured access log = `/v1/*` only** (user-confirmed). `log_requests` middleware in `app.py`; the
  route hands fields to the middleware via `request.state` (`transform_name`, `log_meta`, `error_code`).
  `/health` is excluded from the JSON line but every response still gets `X-Request-Id`. To log a new
  route, ensure its path is under `/v1/` (or widen the middleware's prefix check).
- **`configure_logging` (`logging_setup.py`) is idempotent** (marker attributes on handlers) and sets
  `tts.request` to `propagate=False` so the JSON line is pure. Consequence for tests: pytest `caplog`
  won't capture `tts.request` (no propagation) — attach your own handler to the `tts.request` logger
  instead (see `tests/test_logging.py`'s `_Capture`).

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
  Auth/logging landed in T7 **without** moving to lifespan wiring — `require_api_key` and the
  `log_requests` middleware both read `app.state.settings` (via `_settings()`) at request time, and
  tests still patch `app.state.settings`. `configure_logging` runs once at import off the resolved
  settings. A lifespan-based rewire remains optional, not required.
- Starlette TestClient emits a `StarletteDeprecationWarning` about httpx; harmless, no action.
