# Cycle Log

## T9 — Brickfeed `story-cover` · `opinion-gate` HELD out of charter (2026-07-13)

First of the Brickfeed-requested transforms (`docs/requests/brickfeed-2026-07.md`, provenance
`brickfeed@40acb90`, copied into this repo pre-dispatch). Dispatched as a pair
(`story-cover` + `opinion-gate`); **`opinion-gate` was held** during plan review and only
`story-cover` shipped.

**Decision — `opinion-gate` HELD, escalated to product owner (not built).** The request
frames it as a **fail-closed, safety-load-bearing** topic gate: *exclude anything centering
tragedy, violence, death, disaster casualties, or victims; if uncertain, exclude.* That is
squarely "safety-relevant classification", which DESIGN §1 (line 9) and system-overview §5
declare the service is **not** for. Building it would silently amend the §1 charter — which
CLAUDE.md forbids without a product-owner ADR. Per the plan-mode decision, `story-cover` ships
now and the `opinion-gate` charter call is escalated (see NOTES-FOR-NEXT-CYCLES). The incumbent
Claude gate stays live meanwhile. No `opinion_gate.py` module was created.

**Shipped** — one new production transform, net-new (no DESIGN §7.x section; the module
docstring + this entry are its binding contract).
- `src/tts/transforms/story_cover.py` — `build_story_cover()` [`story-cover`, v0.1.0,
  `qwen3.5:9b`]. Five-field cover bundle: `headline` (10–200), `description` (40–600),
  `imagePrompt` (30–400), `category` (fixed 8-value enum), `caption` (15–160). `options_schema`
  `{}`; `input_budget=1200`, `over_budget=truncate`/`head`; temp 0.4, num_predict 512.
  Validators mirror `image-prompt`'s subject-neutral set: `banned_substrings` on
  imagePrompt/headline/caption/description + `word_range("imagePrompt", 8, 60)`.
- `docs/requests/brickfeed-2026-07.md` — the Brickfeed request doc, imported with a provenance
  header noting it is the *request*, not the contract.
- `tests/fixtures/story_cover/` — 5 synthetic 3-line inputs (`01_bike_lanes` BUSINESS +
  `02_spinning_star` SCIENCE from the request examples; `03_marathon` SPORTS, `04_ai_chip`
  TECHNOLOGY, `05_festival` CULTURE, new).
- `tests/test_story_cover.py` (+7 FakeLLM): binding/shape; happy-path (5 keys, not truncated);
  over-budget single-paragraph **no-op** (truncated stays False); validator-catch 422s for
  imagePrompt banned-substring and >60-word; schema-reject 422s for short headline and
  out-of-enum category.
- `tests/test_gpu.py` — `# --- T9 ---` section: all 5 fixtures through `run_transform` on
  `qwen3.5:9b`, shape/enum/`truncated is False` assertions, outputs printed for the eyeball.

**Reconciled contract vs. requested** (deviations recorded in the module docstring):
1. `category` gets `"type": "string"` alongside its `enum` (house style; request had `enum` only).
2. `imagePrompt`/`caption` held **subject-neutral** (ADR-0004). The request's example outputs
   and preamble ask for style/mood ("cartoon", "jubilant", "playful/cartoonish"); style
   (incl. Brickfeed's toy-brick treatment) is caller-side, never baked in. Template forbids
   style/medium/artist/camera words (and "cartoon"/"photo"); validators enforce shape. The
   request's own subject rules (no text/logos/brands in the scene) are preserved.
3. `imagePrompt` word bound is `word_range(8, 60)` (house `image-prompt` binding), not the
   request preamble's looser "~15–30 words"; template guides "~15–40 words".
4. Truncation is a structural **no-op** for the single-paragraph story-cover input — `head`
   cuts only on blank-line paragraph boundaries (`budget.py`), so an over-budget input passes
   through unchanged and is never rejected. This matches the request's "truncating the tail of
   a long title is harmless" intent.

**Verification**
- `make lint` clean (no `# noqa`; template lines kept ≤100 chars).
- `make test` → **127 passed** (120 prior + 7 new), 8 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7, `qwen3.5:9b`) → **8 passed** (full suite green;
  one earlier run flaked on the pre-existing T5 `cast_canonicalize` sentence-count assertion,
  which passed on isolated re-run and again in the full green run — flakiness noted in NOTES).

**GPU outputs — story-cover on `qwen3.5:9b` (human eyeball; subject-neutral, no style leak):**
- `[01_bike_lanes]` category=POLITICS (1903ms, cold) — headline "City council approves new
  downtown bike lane network"; imagePrompt "A wide paved street with freshly painted green
  lanes running alongside parked cars, under an overcast sky with pedestrians walking on
  sidewalks near modern office buildings".
- `[02_spinning_star]` category=SCIENCE (1986ms) — imagePrompt "A bright glowing sphere
  spinning rapidly in the vast dark void of space with distant faint background stars visible
  around it".
- `[03_marathon]` category=SPORTS (2001ms) — imagePrompt "An athletic woman wearing running
  gear crosses the finish line of an urban street course while holding up her arms in
  celebration with spectators visible nearby".
- `[04_ai_chip]` category=TECHNOLOGY (1899ms) — imagePrompt "Close-up view of an advanced
  computer processor chip with glowing blue circuitry patterns on its surface".
- `[05_festival]` category=CULTURE (1945ms) — imagePrompt "Thousands of colorful paper lanterns
  float on calm water near a wide city riverbank where people stand watching under soft evening
  light with reflections shimmering below".
- All `attempts=1`, `truncated=False`. Category is model judgment on a valid enum (01 chose
  POLITICS where the request example said BUSINESS — both defensible; wording never asserted).

**Template change:** new template, ships at `version 0.1.0`.

**Deviations / decisions**
- `opinion-gate` HELD out of §1 charter (safety-relevant classification), escalated — see above.
- Model binding `qwen3.5:9b` (the standing T3 rebind for every production transform).
- The four reconciliations above (category type, subject-neutral imagery, word bound,
  truncation no-op) are the binding `story-cover` contract; the request doc is not.

## T7 — Ops hardening: listing · auth · logging · systemd · README (2026-07-13)

**Deploy — T7 human box CLOSED (2026-07-13).** Installed under systemd on the 5070 (host G434) per
`deploy/README.md`, keyless (LAN posture, `TRANSFORM_API_KEY` unset). Tree rsynced to
`/opt/text-transform-service`; unit at `/etc/systemd/system/text-transform-service.service`; runs as
`User=kb`. Verified: `systemctl status` → **active (running)**; `is-enabled` → **enabled**; startup log
`startup model check: all 1 bound model(s) present in Ollama`; `curl :8712/health` →
`{"status":"ok","ollama_reachable":true,"models_loaded":[],"uptime_s":25}`.
- **Deviation 1 — `User=kris`→`User=kb`.** DESIGN §9's `User=kris` account does not exist on this box
  (only `kb`, uid 1000); systemd would fail with "Failed to determine user credentials". Repo unit fixed
  to `User=kb` (operator decision).
- **Deviation 2 (deploy-doc bug, fixed) — `sudo uv sync` → `uv sync` as the service user.** `sudo uv sync`
  built the venv against a **root-managed** CPython under `/root/.local/share/uv/python/…` (mode 700),
  which the `User=kb` service cannot exec → `status=203/EXEC`. Rebuilt the venv as `kb` (no sudo) so the
  interpreter lives under `/home/kb` and is reachable; `deploy/README.md` §2 corrected accordingly.
- **Reboot-survival** left for the operator to confirm (unit is `enabled`; `After=`/`Wants=ollama.service`
  order it behind Ollama on boot). **M1 status: TTS is deployed and feature-complete on the 5070.**

**Shipped** — the service is now **deployable and pleasant to operate**; no new transforms, no
pipeline behavior change. With this cycle TTS is **feature-complete for M1**, pending the human deploy.
- `app.py` — `GET /v1/transforms` (DESIGN §4): serializes the registry sorted by name via
  `_serialize_transform()`, projecting exactly `name, version, model, input_budget, over_budget,
  options_schema, output_schema`. The internal Jinja `template` and Python `validators` are never
  emitted.
- `app.py` — **auth** (ADR-0003): `require_api_key` dependency on the three `/v1/*` routes (transform,
  listing, unload). No-op unless `Settings.auth_enabled` (i.e. `TRANSFORM_API_KEY` set); when enabled a
  missing/wrong `X-Transform-Key` header raises `TransformError(401, "unauthorized", …)`. A new global
  `@app.exception_handler(TransformError)` serializes dependency-raised errors into the standard §4
  envelope (the transform route keeps its inline catch so a genuine bug still maps to 500). `/health`
  has no auth dependency — always open.
- `app.py` — **structured logging + `X-Request-Id`** (DESIGN §9): a `log_requests` HTTP middleware mints
  a `uuid4().hex[:8]` request id, sets `X-Request-Id` on **every** response, and emits one JSON line on
  the `tts.request` logger for **`/v1/*`** requests (`ts, request_id, transform, status`, plus
  `attempts, input_tokens_est, truncated, queued_ms, latency_ms` from a completed run and `error_code`
  on failures). `/health` is excluded from the access log (polled too often) but still gets the header.
  The transform route stashes `transform_name`/`log_meta`/`error_code` on `request.state`.
- `logging_setup.py` (new) — `configure_logging(level)`: idempotent handler install. `tts.request` gets a
  pure-`%(message)s` handler with `propagate=False` (so the JSON line is never prefix-wrapped); `tts.*`
  diagnostics get a timestamped human handler. Finally consumes the previously-inert `TTS_LOG_LEVEL`.
- `deploy/text-transform-service.service` (new) — the DESIGN §9 systemd unit, path-adjusted, plus one
  add: `EnvironmentFile=-/opt/text-transform-service/.env` so `TRANSFORM_API_KEY`/`TTS_ENV` can be
  supplied without editing the unit (`-` prefix → optional file, runs keyless if absent).
- `deploy/README.md` (new) — install steps: rsync to `/opt`, `uv sync`, optional `.env` (auth + prod
  env), `systemctl` install/enable, verify `/health` + `journalctl`, reboot check; plus a "check the
  unit" section flagging `User=kris`, paths, and hardcoded host/port for human adjustment.
- `README.md` — completed: status → T7; API summary table (all four endpoints + auth column); `401
  unauthorized` added to the error taxonomy; `GET /v1/transforms`, `Authentication`, and `Operability`
  sections; the 8-step "adding a transform" recipe; a Development/testing section documenting the two
  `book/` fixture globs and the `wants_options` convention. Bindings shown as `qwen3.5` (T3 rebind).
- Tests (+15): `test_transforms_listing.py` (array shape, exactly-7-fields/no leaked internals, known
  binding, sorted); `test_auth.py` (missing/wrong/correct key on transform; listing + unload gated;
  `/health` open; auth-off allows no header); `test_logging.py` (one parseable JSON line per `/v1/*`
  request with meta fields; `X-Request-Id` matches the logged id; error line carries `error_code`;
  `/health` not access-logged but still gets the header).

**Verification**
- `make lint` clean; `make test` → **120 passed** (105 prior + 15 new), 7 gpu deselected.
- **Live spot-check on the 5070** (Ollama 0.30.7, `TTS_ENV=dev`, `TRANSFORM_API_KEY=secret`):
  - `GET /health` (no key) → **200**, `X-Request-Id: fb4251f7`; **no** access-log line (excluded).
  - `GET /v1/transforms` (no key) → **401** `{"error":{"code":"unauthorized",…}}`; **with** key → **200**.
  - `POST /v1/transform/echo` (no key) → **401**; with key → **200** `{"output":{"echo":"First sentence."}}`.
  - Parsed `tts.request` lines (all valid JSON):
    ```json
    {"ts":"2026-07-14T00:20:13.058829+00:00","request_id":"321d7f5d","transform":null,"status":401,"error_code":"unauthorized"}
    {"ts":"2026-07-14T00:20:13.070442+00:00","request_id":"078f3743","transform":null,"status":200}
    {"ts":"2026-07-14T00:20:23.266559+00:00","request_id":"528d666b","transform":"echo","status":200,"attempts":1,"input_tokens_est":6,"truncated":false,"queued_ms":0,"latency_ms":3609}
    ```
  - Listing body: sorted `[cast-canonicalize, cast-mentions, echo, illustration-prompt, image-prompt,
    scene-update]`; first entry carries both schemas as objects, `leaked_template:false`,
    `leaked_validators:false`.

**Deviations / notes**
- **`EnvironmentFile` added to the systemd unit** — the one substantive change beyond §9-verbatim, so the
  key/env can be set without editing the committed unit. Optional (`-` prefix); keyless still works.
- **§9 systemd-vs-config tension (filed to NOTES):** the unit's `ExecStart` hardcodes `--host 0.0.0.0
  --port 8712` and does **not** read `TTS_HOST`/`TTS_PORT`. Left verbatim by design; `deploy/README.md`
  flags it and the env file covers the auth key.
- **Log scope = `/v1/*` only** (user-confirmed): `/health` polls are excluded from the JSON access log
  to keep it operable; every response still carries `X-Request-Id`. Faithful to §9's field set, which is
  transform-shaped.
- **Global `TransformError` handler added** alongside the transform route's existing inline catch — the
  handler serves dependency-raised (auth) errors on all `/v1/*` routes; the inline catch stays so a
  genuine pipeline bug still maps to 500 rather than being caught by the handler.
- **Human-pending:** the systemd install itself (rsync → `uv sync` → enable → reboot-survives → `/health`
  ok) is the one open acceptance box — `deploy/` is prepared; the human runs it on the 5070.
- No out-of-scope discoveries.

## T6 — `scene-update` + `illustration-prompt` + soft `meta.warnings` (2026-07-13)

**Shipped** — with T6 the service covers **every Scriptorium bake transform** (P1 cast-mentions,
P2 cast-canonicalize, P3 scene-update, P5 illustration-prompt).
- `transforms/scene_update.py` — `build_scene_update()`, verbatim DESIGN §7.4: the 8-field ledger
  `output_schema` (`location`/`time_of_day` enum/`atmosphere`/`present`/`scene_changed`/
  `visual_salience` [0,1]/`best_visual_beat` 15–220/`carry_notes`), the §7.4 `options_schema`
  (`prior_ledger` object-or-null, `cast_names` ≤40, optional `era`), the SYSTEM/USER template, budget
  **1600 est-tokens `over_budget="reject"`** (paginator-bug posture → 413), temp 0.2, num_predict 500,
  validator `banned_substrings("best_visual_beat", ["\n"])`. Called once per page strictly in order;
  the caller threads each returned ledger into the next call's `prior_ledger`.
- `transforms/illustration_prompt.py` — `build_illustration_prompt()`, verbatim DESIGN §7.5:
  `output_schema` (`prompt` 60–600, `depicted` ≤4, `shot` enum, optional `avoid`), `options_schema`
  (`ledger` object, `cast` ≤6 of `{name,one_line}`, optional `era`), the SYSTEM/USER template (reads
  `options.ledger`/`cast`/`era` + the `{% for c in options.cast %}` loop), budget 1600 `reject`, temp
  0.6, num_predict 350, validators `word_range("prompt", 20, 90)`, `banned_substrings("prompt",
  ["**","\n","style of","photograph","oil painting","watercolor","engraving"])`, and the **soft**
  `depicted_subset_of_cast()`.
- `pipeline.py` — **soft-validator mechanism**: a validator reason prefixed `"warn:"` is recorded to
  `meta.warnings[]` and never fails/retries the request; any other non-`None` reason stays a hard
  failure (retry → 422), unchanged. Warnings come only from the *successful* attempt (a rejected/retried
  attempt's warnings are dropped). `meta.warnings` is **omitted when empty**, so §4's meta shape is
  unchanged in the common case (additive-only). `_attempt_reason` now takes `options` and returns
  `(output, reason, warnings)`.
- `validators.py` — `depicted_subset_of_cast()`: options-aware soft validator (DESIGN §7.5
  `depicted ⊆ cast-names-or-empty`). Options-aware validators opt in via a `wants_options` marker; the
  pipeline then calls them `validator(output, options)`. Existing validators untouched (still single-arg).
- `transforms/__init__.py` — both new transforms registered **unconditionally** (production).
- `tests/fixtures/book/` — extended with **3 consecutive** *Time Machine* (PG #35) pages `page_a`
  (800 w) / `page_b` (791 w) / `page_c` (712 w) covering the Ch. I dinner argument → Ch. II model-machine
  demonstration → the vanishing (§7.5's worked micro-example beat), a **stable smoking-room location** so
  the eyeball can confirm location carries across non-moving pages. Plus `scene_start.json` (page-1
  options, `prior_ledger: null`) and `illustration_cast.json` (the T5 canonical Time Traveller `one_line`
  as an illustration cast entry). Full book not committed.
- Tests (+14): `test_pipeline.py` (soft warn → 200 + `meta.warnings`, no retry; no-warning omits the key;
  a discarded attempt's warning never surfaces); `test_validators.py` (`depicted_subset_of_cast`);
  `test_scene_update.py` (binding/shape, `prior_ledger` object+null happy paths, over-budget → 413 without
  calling the LLM, missing required ledger field drives the schema-retry path → 422);
  `test_illustration_prompt.py` (binding/shape, cast entry missing `one_line` → 400, medium-word
  "watercolor" → 422, depicted-not-in-cast → **200 + warning**, happy path with no warnings);
  `test_gpu.py` (thread the 3 pages sequentially, then illustration-prompt on the max-salience page).

**Verification**
- `make lint` clean; `make test` → **105 passed** (91 prior + 14 new), 7 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7, qwen3.5:9b) → **7 passed** in ~91s (6 prior + 1 new).
- Live route (`TTS_ENV=prod`): `/health` ok; `POST /v1/transform/scene-update` (prior_ledger null) → 200
  with the full 8-field ledger (`meta` has no `warnings` key); over-budget page → **413 `over_budget`**
  (`input_tokens_est:1756, budget:1600`) before any generation; `illustration-prompt` with a cast entry
  missing `one_line` → **400 `bad_options`**. All via the §4 envelope.

**GPU outputs (qwen3.5:9b, all `attempts:1`)** — sequential threading, cold 11382 ms / warm 7434, 7026 ms:
- `page_a` (salience **0.45**) — location *"The Time Traveller's study"*, evening, atmosphere *"warm,
  intellectual, after-dinner"*, present = all six diners; beat: *"The Time Traveller's grey eyes shine and
  twinkle as he leans forward with a lean forefinger to mark points on the air…"* — `scene_changed:false`.
- `page_b` (salience **0.72**) — **same location** (*"The Time Traveller's study"*), atmosphere gains
  *"speculative"*; beat: *"The Time Traveller leans forward with a lean forefinger to trace the movement of
  mercury along an invisible line on the air…"* — `scene_changed:false`.
- `page_c` (salience **0.95**) — **same location**, atmosphere *"wondrous, suspenseful, magical"*; beat:
  *"The model machine swings round and becomes a ghostly eddy of faintly glittering brass and ivory before
  vanishing from the table."* — the §7.5 beat, correctly the highest-salience page.
- `illustration-prompt` on `page_c` (7390 ms) — prompt weaves the Time Traveller's identifiers verbatim
  from the canonical entry (*"an old scientist in a dirty green-smeared coat with pale face and grey
  hair…"*) around the vanishing beat; `shot: wide`; `depicted: [the Time Traveller, Filby, the
  Psychologist, the Medical Man]`. **The soft validator fired live and non-fatally:**
  `meta.warnings = ["depicted not in cast: ['Filby', 'the Psychologist', 'the Medical Man']"]` (only the
  Time Traveller was in the single-entry cast) — recorded, still 200.

**Eyeball** (human): location carries correctly across the three non-moving pages
(study→study→study); salience rises monotonically to the vanishing; beats are concrete present-tense
sentences; the illustration prompt uses the character's visual identifiers, not a bare name.

**Template change** — none. Both §7.4/§7.5 templates ship byte-verbatim (a diff of each module template
against the DESIGN code-fence is IDENTICAL); both `version` stay `0.1.0`.

**Deviations / notes**
- **Binding rebind (carried from T3):** §7.4/§7.5 name `qwen3:8b` (absent); both transforms bind
  `qwen3.5:9b` (human-approved, `docs/models.md`). §7.5's optional `qwen3:14b` swap is a future note, not
  this cycle.
- **Verbatim template + long line:** §7.4's "Known cast … `{{ options.cast_names | join(", ") }}`" line is
  102 chars; kept byte-verbatim via the T5 adjacent-literal split (no newline at the join). §7.5 has no
  over-100 line, so it uses image_prompt.py's `'''…'''` style (which safely holds the embedded `"""`).
- **`meta.warnings` omit-when-empty** (confirmed with the user): keeps §4's 8-key meta shape and the
  existing exact-key pipeline test green; consumers check `meta.get("warnings")`.
- **Options-aware validators** opt in via `wants_options` rather than a uniform 2-arg signature — smallest
  blast radius, keeps the common `Validator` contract single-arg.
- **T5 GPU test glob tightened** from `*.txt` to `0*.txt` so the new `page_*.txt` fixtures don't inflate
  its "expected 4 excerpts" count.
- `carry_notes` came back `""` on all three pages (schema-valid: no `minLength`); the model didn't
  accumulate continuity facts here — a model choice, not a mechanism issue. GPU assertions stay shape-only.
- No out-of-scope discoveries.

## T5 — `cast-mentions` + `cast-canonicalize` (2026-07-13)

**Shipped**
- `transforms/cast_mentions.py` — `build_cast_mentions()`, verbatim DESIGN §7.2: mentions-array
  output_schema (per-item `name`/`aliases`/`descriptors`/`is_person`, `maxItems:15`), the
  SYSTEM/USER template, budget **1600 est-tokens with `over_budget="reject"`** (a page over budget
  is a paginator bug → 413, never truncate), temp 0.2, num_predict 700, `options_schema={}`,
  validator `no_empty_strings("mentions[].name")`.
- `transforms/cast_canonicalize.py` — `build_cast_canonicalize()`, verbatim DESIGN §7.3: the §7.3
  `options_schema` (`required:[name,descriptors]`), output_schema (`visual_description` 80–700,
  `one_line` 15–160, `tags` ≤8), the SYSTEM/USER template (reads `options.*` via Jinja), budget 1200
  `truncate`/`head`, temp 0.5, num_predict 400, validator
  `banned_substrings("visual_description", ["**","\n\n","personality","brave","kind"])`.
- `validators.py` — **nested-field extension**: `no_empty_strings(field)` now accepts a one-level
  array-of-objects path `"<array>[].<sub>"` (e.g. `mentions[].name`) in addition to the top-level
  list form. Catches a whitespace-only `name` that slips past the schema's `minLength:1`. Top-level
  behavior unchanged. (Resolves the T2/T3 carried-forward blocker.)
- `transforms/__init__.py` — both cast transforms registered **unconditionally** alongside
  `image-prompt` (production; `echo` stays dev-gated).
- `tests/fixtures/book/` — 4 excerpts from *The Time Machine* (Project Gutenberg #35, public domain;
  PG boilerplate stripped, full book not committed), each 555–608 words covering the four §-cases:
  `01_dialogue` (multi-character dialogue + physical descriptors), `02_description` (pure
  time-travel description, zero named characters), `03_introduction` (first Eloi introduction),
  `04_pronouns` (established character carried by pronouns/epithets only). Plus
  `canonicalize_time_traveller.json` — hand-assembled options payload with 8 verbatim descriptors
  drawn from `01_dialogue`.
- Tests: `test_validators.py` (nested-path standalone); `test_cast_mentions.py` (FakeLLM —
  binding/shape, over-budget → 413 **without calling the LLM**, nested validator catches a
  whitespace name → 422, happy path, empty-mentions valid); `test_cast_canonicalize.py` (FakeLLM —
  binding/shape, missing `descriptors` → 400 `bad_options`, banned personality-word → 422, happy
  path); `test_gpu.py` — all 4 excerpts through cast-mentions on **qwen3.5:9b** (loose zero-character
  check, mentions printed) and the canonicalize payload (≤160-char `one_line`, 2–4 sentence
  `visual_description`, printed).

**Verification**
- `make lint` clean; `make test` → **91 passed** (81 prior + 10 new), 6 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7, qwen3.5:9b) → **6 passed** in ~57s (4 prior + 2 new).
- Live route (`TTS_ENV=prod`): `/health` ok; `POST /v1/transform/cast-mentions` on `04_pronouns` →
  200 schema-valid, `attempts:1`; a ~1300-word body → **HTTP 413** `over_budget`
  (`input_tokens_est:1756`, `budget:1600`) via the §4 error envelope; `POST /v1/transform/
  cast-canonicalize` with the Time-Traveller payload → 200 paintable entry.

**GPU outputs — human eyeball (qwen3.5:9b, `attempts:1` on every call)**

_cast-mentions_ (cold first call 10998 ms; warm 1507–7042 ms):
- `01_dialogue` → 6 person mentions: the Time Traveller, the Editor, the Doctor, the Journalist,
  the Psychologist, the Medical Man. Time Traveller descriptors are verbatim quotes
  ("His coat was dusty and dirty, and smeared with green down the sleeves; his hair disordered…").
- `02_description` (pure description) → **1** mention: the Time Traveller (the lone first-person
  narrator) — correctly invented **no cast**; descriptor "helpless headlong motion".
- `03_introduction` → 2 mentions ("I" is_person:true, descriptor "fragile thing out of futurity";
  "He" is_person:false — the fragile Eloi).
- `04_pronouns` → 3 mentions: the Time Traveller (epithet, correctly picked up), "I", the
  man-servant — the established-character-via-epithet case works.

_cast-canonicalize_ ("the Time Traveller", 7312 ms) → paintable, 3 sentences, drawn from the evidence:
- one_line: *Old scientist in dirty green-smeared coat with pale face, grey hair, cut chin, and
  limping walk.*
- visual_description: *An elderly man with disordered, greyer hair stands wearing a dusty and dirty
  coat smeared with green down the sleeves. His face is ghastly pale, marked by a brown cut on his
  chin that remains half-healed, while an intense suffering draws him into a haggard expression where
  only the ghost of an old smile flickers across his features. He walks with a limp resembling those
  of footsore tramps and wears tattered, blood-stained socks.*
- tags: *['grey beard', 'dusty coat', 'blood-stained socks', 'ghastly pale face', 'half-healed cut']*

**Template change:** none. Both transforms produced schema- and validator-valid output on the first
attempt across all fixtures, so the §7.2/§7.3 templates ship verbatim and both `version` stay `0.1.0`.

**Observed model quirks (no action — schema/validators only assert shape):** the model occasionally
emits an empty descriptor string (`""`) instead of `[]` and once truncated a descriptor with a stray
non-Latin token; both are schema-valid and the §7.2 validator only guards `name`. Noted for the
downstream caller's reduction step, not this service.

**Deviations / decisions**
- **Model binding `qwen3.5:9b`, not §7.2/§7.3's `qwen3:8b`.** The literal tag is absent; this is the
  human-approved T3 rebind (same weight class; `docs/models.md`), not a template change.
- **Both cast transforms registered in every environment** (production, like `image-prompt`).
- **§7.3 template kept byte-verbatim despite two lines >100 chars.** Two of §7.3's Jinja control-flow
  lines exceed the ruff 100-char limit. Rather than reflow the prompt (which would be a template
  change), the template literal is split at those two points into adjacent string literals — **no
  newline introduced at the join**, so the rendered string is byte-identical (verified). Matches
  `pipeline.py`'s `COMMON_FRAMING` style; no ruff-config change needed.

## T4 — `image-prompt` transform (2026-07-13)

**Shipped**
- `transforms/image_prompt.py` — `build_image_prompt()`, the first **production** transform,
  verbatim from DESIGN §7.1: output_schema (`prompt` string, 30–400 chars), the SYSTEM/USER
  template, budget 3000 est-tokens with `lede_first_n` truncation, temp 0.4, num_predict 160,
  `options_schema={}`, validators `banned_substrings("prompt", ["**","##","http","\n"])` +
  `word_range("prompt", 8, 60)`. No new pipeline/validator/budget code — pure composition of
  existing T2/T3 seams.
- `transforms/__init__.py` — `register_all` now registers `image-prompt` **unconditionally**
  (production transforms register in every env; `echo` stays dev-gated).
- `tests/fixtures/news/` — 5 synthetic wire-service stories (all invented; no real articles):
  `01_quake` (~384 w), `02_transit` (~419 w), `03_multitopic` (~332 w, bundled 3-story roundup),
  `04_science` (~441 w), `05_flood_long` (**2311 w / 3120 est-tokens**, 28 paras — exercises
  truncation).
- Tests: `test_image_prompt.py` (FakeLLM) — binding/shape; short fixture happy path
  (`truncated:false`); long fixture → `meta.truncated:true` + post-trunc est ≤ 3000; markdown-
  polluted response → 422 with banned-substring reasons; `word_range` rejects too-few AND too-many
  words. `test_gpu.py` — all 5 fixtures through the real pipeline on **qwen3.5:9b**, schema+validators
  enforced by the pipeline (no-raise = pass), long fixture asserts `truncated:true`, prompts printed.

**Verification**
- `make lint` clean; `make test` → **81 passed** (75 prior + 6 new), 4 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7, qwen3.5:9b) → **4 passed** in ~23s.
- Live route (`TTS_ENV=prod`, so no dev gate): `/health` ok; `POST /v1/transform/image-prompt`
  short fixture → 200 schema-valid, `truncated:false`, `latency_ms` 2188; long fixture →
  `truncated:true`, `input_tokens_est:2918` (post-truncation), schema-valid one-line prompt.

**GPU outputs — all 5 fixtures (qwen3.5:9b, human eyeball; `attempts:1` on every one)**
- Cold-load latency: 6431 ms (first fixture). Warm: ~2250–3492 ms.
1. `01_quake` (truncated=False, 6431 ms cold) →
   *A fallen century-old brick clock tower lies shattered on a cold mountain town square at dawn,
   with cracked copper face and twisted iron frame amidst rubble while emergency crews clear debris
   under floodlights.*
2. `02_transit` (truncated=False, 2250 ms) →
   *A pale green tram with its bell ringing glides along a newly laid waterfront line where gulls
   wheel overhead and a brass band plays on the quay near an old fish market.*
3. `03_multitopic` (truncated=False, 2305 ms) →
   *Dawn sky filled with sixty hot-air balloons rising together over a grass meadow outside Dell,
   including an enormous green turtle balloon drawing cheers from children lining a fence while
   spectators watch below.* — correctly picked the most visual of 3 bundled topics (balloons over
   the budget-vote and dairy-price stories), per the §7.1 rule.
4. `04_science` (truncated=False, 2313 ms) →
   *A remotely operated submersible illuminates a dense wall of pale pink and orange cold-water
   coral rising in terraces along an underwater canyon slope at two thousand metres depth.*
5. `05_flood_long` (**truncated=True**, 3492 ms) →
   *A low brick house stands on a muddy porch roof with two children inside as a rowboat rescues
   them from brown floodwater carrying fence posts and plastic barrels under an overcast sky.*

**Template change:** none. All 5 fixtures produced schema- and validator-valid one-line prompts on
the first attempt, so the §7.1 template ships verbatim and `version` stays `0.1.0`.

**Deviations / decisions**
- **Model binding `qwen3.5:9b`, not §7.1's `qwen3:8b`.** The §7.1 literal is absent on the box; this
  is the human-approved T3 rebind (same weight class; see `docs/models.md`), not a template change.
  Recorded on the transform and here.
- **`image-prompt` registered in every environment** (unlike dev-gated `echo`). It is a production
  transform. Verified the one prod-env route test (`echo`→404) still holds.

## T3 — Ollama client, constrained decoding, concurrency, unload (2026-07-13)

**Shipped**
- `llm.py` — `OllamaClient` implementing `LLMClient` + `LLMBackendError`. `chat` does
  `POST /api/generate` (`stream:false`, `format`=output_schema when non-empty, top-level
  `think`/`keep_alive`, `options:{temperature,top_p,num_predict}`), returns the raw
  `response` text; 120s timeout. Helpers `list_tags` (`/api/tags`), `list_loaded`
  (`/api/ps`), `unload` (`/api/generate` `keep_alive:0`). httpx/parse failures → `LLMBackendError`.
- `pipeline.py` — the per-request `params` now carries `"model": transform.model` (the
  protocol's `chat` has no model arg; one shared client serves every binding). The `chat`
  call is wrapped: `LLMBackendError` → `TransformError(503, model_unavailable)`, **not
  retried** (infra failure ≠ validation failure); the semaphore `finally` still releases.
- `startup.py` — `warn_missing_models`: diffs registry-bound models against `/api/tags`,
  logs a loud warning for any missing; never raises (Ollama-down is itself just a warning).
- `app.py` — `app.state.llm` is now a real `OllamaClient` (constructor opens no sockets); a
  `lifespan` runs the startup model check (fires only under the ASGI lifespan protocol, so
  bare-`TestClient` unit tests make no network calls). Added `POST /v1/models/unload`
  (`{"model"}` or `{}`→all loaded; unload each, then **bounded-poll** `/api/ps` to confirm;
  returns `{"unloaded":[…]}`; backend failure → 503 `model_unavailable`). Auth-exempt (T7).
- `transforms/echo.py` — rebound `qwen3:0.6b` → `qwen3.5:2b`.
- Tests: `test_ollama_client.py` (respx: generate body shape — model/think/format top-level,
  sampling under `options`, `stream:false`; empty schema omits `format`; http/conn/parse
  errors → `LLMBackendError`; tags/ps parse; unload posts `keep_alive:0`), `test_startup_check.py`
  (missing→warn, present→quiet, empty registry noop, unreachable→warn-not-raise), pipeline
  (second concurrent request `queued_ms>0`; `LLMBackendError`→503 not retried; params carry
  model), route (unload one/all/backend-error-503). `test_gpu.py` (echo schema-valid on
  `qwen3.5:2b`; constrained decoding forces schema from a non-JSON prompt; unload empties
  `/api/ps`). README documents unload + constrained decoding + bindings; models.md resolved.

**Verification**
- `make lint` clean; `make test` → **75 passed** (55 prior + 20 new), 3 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7 up) → **3 passed** in ~6.3s.
- Live boot (`TTS_ENV=dev`): `/health` ok; `POST /v1/transform/echo` → 200 schema-valid
  `{"echo": …}` from `qwen3.5:2b`, `latency_ms` ~3.9–4.1s (cold load), `attempts:1`; unknown
  → 404; malformed body → 400. `POST /v1/models/unload {}` → `{"unloaded":["qwen3.5:2b"]}`
  and `/api/ps` empty afterward.

**Task 0 — model rebind + think verification (human decision already made)**
- Rebound the absent `qwen3:8b`/`qwen3:0.6b` → **`qwen3.5:9b`** (default) / **`qwen3.5:2b`**
  (test/echo), same weight classes in the installed family, no pulls. Recorded in
  `docs/models.md`.
- **Think disable VERIFIED live:** the top-level `think: false` request field suppresses the
  `thinking` output on `qwen3.5` (Ollama 0.30.7); `think: true` brings it back (confirmed
  contrast). `/no_think` prompt tag unnecessary.

**Deviations / decisions**
- **`/api/generate`, not `/api/chat` (DESIGN §5).** Verified empirically & deterministically
  that on this Ollama (0.30.7) `POST /api/chat` **silently ignores `format`** (no constrained
  decoding — prose returned for a non-JSON prompt), while `POST /api/generate` **enforces**
  the schema grammar. To keep ADR-0002 / §1 ("format drift structurally impossible") true, the
  client uses `/api/generate`; rendered `[{system},{user}]` map to `system`+`prompt`. **Human-
  approved** during the cycle. Full record in `docs/models.md`. `/health` still uses `/api/ps`
  + `/api/tags`.
- **Model rebind** deviates from DESIGN §2 tags (see Task 0 / `docs/models.md`).
- **`model` added to the pipeline `params` dict** so the shared client can target each
  transform's tag through the fixed 3-arg `chat` signature (non-breaking: T2 tests assert
  `params["temperature"]` by key).
- **Unload confirmation bounded-polls `/api/ps`** because Ollama doesn't drop a model the
  instant `keep_alive:0` returns; a single read under-reported. Poll ≤ ~1.8s.

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
