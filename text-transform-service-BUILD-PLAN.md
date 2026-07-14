# text-transform-service — Build Plan

**Status:** Approved — 2026-07-13
**Executor:** Claude Code (Sonnet). One cycle per dispatch. Read `system-overview.md` and `text-transform-service-DESIGN.md` (both at repo root) before every cycle.

## 0. Cycle discipline (applies to every cycle)

1. **Plan mode first.** Restate the cycle's scope in your own words, list the files you will touch, list the tests you will add, and identify anything ambiguous. If genuinely ambiguous, stop and ask; do not improvise around the DESIGN doc.
2. **Scope is a fence.** Implement exactly the cycle's "In scope." Anything discovered along the way goes into `NOTES-FOR-NEXT-CYCLES.md` at repo root, not into code.
3. **ADR-first where marked.** ADRs are written before the code they govern, in `docs/adr/NNNN-title.md`, using the template in `docs/adr/0000-template.md` (created in T1). ADR content for 0001–0005 is already written in DESIGN §2 — transcribe, don't re-argue.
4. **Tests land in the same cycle as the code they cover.** A cycle with untested new logic is incomplete.
5. **Definition of done, every cycle:** `uv run ruff check .` clean; `uv run pytest -m "not gpu"` green; acceptance checklist below fully satisfied; `README.md` updated if user-facing behavior changed; a short `CYCLE-LOG.md` entry appended (cycle id, date, what shipped, deviations).
6. **Never assert exact LLM wording in any test.** Shape, schema, bounds only.
7. **Commit style:** one commit per logical step, message prefixed `T{n}:`.

## 0.1 Environment prerequisites (human does these once, before T1)

On the 5070 box (Ubuntu):
```bash
# Ollama installed and running as a service (verify: systemctl status ollama)
curl -fsSL https://ollama.com/install.sh | sh   # if not already installed
ollama pull qwen3:8b
ollama pull qwen3:0.6b
# qwen3:14b deferred until a transform needs it
# uv installed (verify: uv --version)
```
Record the exact resolved tags (`ollama list`) in `docs/models.md` during T1. If tags in DESIGN §2 no longer exist, the human picks replacements in the same weight class and updates `docs/models.md` — the executor never silently substitutes models.

## 0.2 Cycle index

| Cycle | Title | Needs | Size |
|---|---|---|---|
| T1 | Scaffold, ADRs, /health | — | S |
| T2 | Registry + pipeline + FakeLLM + `echo` transform | T1 | M |
| T3 | Ollama client, constrained decoding, concurrency | T2 | M |
| T4 | `image-prompt` transform | T3 | S |
| T5 | `cast-mentions` + `cast-canonicalize` | T3 | M |
| T6 | `scene-update` + `illustration-prompt` | T5 | M |
| T7 | Ops hardening: listing, unload, auth, logging, systemd, README | T3 | S |
| T8 | Brickfeed bench harness | T4 | Deferred — do not build unless dispatched |
| T9 | Brickfeed `story-cover` (shipped); `opinion-gate` HELD out of §1 charter | T4 | S |
| T10 | Brickfeed `opinion-piece` + `opinion-image-brief` (charter-check `opinion-piece` first) | T9 | M |

T4, T5/T6, and T7 are independent of each other after T3; dispatch order above is the default.
T9/T10 build the Brickfeed request pairs from `docs/requests/brickfeed-2026-07.md`.

---

## Cycle T1 — Scaffold, ADRs, /health

**Goal:** a running FastAPI service with health reporting and the decision record in place.

**In scope**
- `uv init`; Python 3.12; deps: `fastapi`, `uvicorn[standard]`, `httpx`, `jinja2`, `pydantic>=2`, `jsonschema`; dev deps: `pytest`, `pytest-asyncio`, `ruff`, `respx` (httpx mocking).
- Repo layout per DESIGN §6 (empty `transforms/` package with `__init__.py`).
- `docs/adr/0000-template.md` (Status/Context/Decision/Consequences) and ADRs 0001–0005 transcribed from DESIGN §2.
- `docs/models.md` recording pulled tags.
- Config module `tts/config.py` reading the §9 env vars with defaults.
- `GET /health` per DESIGN §4: calls Ollama `/api/ps` and `/api/tags` with a 3s timeout; returns `ok`/`degraded`; never raises.
- `Makefile` or `justfile`: `dev` (uvicorn --reload), `test`, `test-gpu`, `lint`.
- `.gitignore`, `README.md` stub (what it is, how to run, port).

**Out of scope:** any transform, any generation, auth.

**Steps**
1. Scaffold project + tooling; commit.
2. ADRs; commit.
3. Config module + tests (env override behavior); commit.
4. `/health` with respx-mocked Ollama (reachable and unreachable cases); commit.

**Acceptance**
- [ ] `just dev` (or `make dev`) serves; `curl :8712/health` returns JSON with `ollama_reachable: true` when Ollama is up.
- [ ] Stop Ollama (or point `OLLAMA_URL` at a dead port): `/health` returns 200 with `status: "degraded"`, does not 500.
- [ ] `pytest -m "not gpu"` green; ruff clean.
- [ ] ADR files 0000–0005 exist and match DESIGN §2 decisions.

---

## Cycle T2 — Registry, pipeline, FakeLLM, `echo` transform

**Goal:** the entire request pipeline of DESIGN §3 working end-to-end with a fake LLM — every error code reachable and tested before a real model is ever involved.

**In scope**
- `registry.py`: `Transform` dataclass exactly as DESIGN §6; `register()`; duplicate-name → startup error; `transforms/__init__.py` explicit import list.
- `llm.py`: `LLMClient` protocol; `FakeLLMClient(responses: list[str] | Callable)` recording `(messages, format_schema, params)` per call.
- `budget.py`: `estimate_tokens(text)` (`ceil(words × 1.35)`); `lede_first_n` and `head` strategies per DESIGN §5, paragraph-boundary splitting on blank lines; both return `(text, truncated: bool)`.
- `pipeline.py`: full §3 pipeline. Semaphore present (size 1) but trivially passed in tests. Retry policy with temp bump. Structured error responses per §4 table (all of 400/404/413/422/503/500).
- Route `POST /v1/transform/{name}` wired to pipeline with app-state LLM client (FakeLLM in tests via dependency override).
- Dev-only transform `echo` (registered only when `TTS_ENV=dev`): options `{}`; output schema `{"type":"object","required":["echo"],"properties":{"echo":{"type":"string"}}}`; template instructs returning the first sentence — with FakeLLM this is just plumbing proof.
- Validator library per DESIGN §6 with unit tests.

**Out of scope:** real Ollama calls, real transforms, `/v1/transforms` listing, auth.

**Acceptance**
- [ ] Unit tests cover: token estimate; both truncation strategies (golden fixtures incl. text with no blank lines); options-schema violation → 400 `bad_options`; unknown name → 404; over-budget+reject → 413; over-budget+truncate sets `meta.truncated`; FakeLLM returning invalid-then-valid JSON → success with `attempts: 2` and bumped temperature visible in FakeLLM's recorded params; FakeLLM always-invalid → 422 with `detail.reasons` length = retries+1; each validator.
- [ ] `POST /v1/transform/echo` with FakeLLM returns 200 with `output.echo` and full `meta` block (all §4 fields present).
- [ ] ruff clean; non-gpu tests green.

---

## Cycle T3 — Ollama client, constrained decoding, concurrency

**Goal:** real generation with schema-constrained output; the service is now genuinely functional.

**In scope**
- `OllamaClient` implementing `LLMClient`: `POST /api/chat`, `stream: false`, `format` = the transform's `output_schema`, `options: {temperature, top_p, num_predict}`, `think` field per DESIGN §2 Qwen3 note (**verify the current Ollama field name against Ollama docs/`ollama --version` behavior; record findings in `docs/models.md`**), `keep_alive` from config. 120s timeout. Ollama-level errors → `model_unavailable` 503.
- Startup check: compare bound models (registry) against `/api/tags`; log loud warning for missing.
- Real semaphore + queue timeout → 503 `busy` (test with FakeLLM that sleeps).
- `POST /v1/models/unload` per DESIGN §4 (generate with `keep_alive: 0`, confirm via `/api/ps`).
- GPU test marker plumbing (`pytest.ini` marker declaration; `-m gpu` skipped by default via marker filter in CI-less local runs — document `just test-gpu`).
- GPU tests (run manually on the 5070): `echo` transform against `qwen3:0.6b` returns schema-valid JSON; unload endpoint empties `/api/ps`; queue-timeout behavior sanity check.

**Out of scope:** production transforms, logging polish, auth.

**Acceptance**
- [ ] On the 5070 with Ollama up: `curl -X POST :8712/v1/transform/echo -d '{"text":"Hello world. Second sentence."}' -H 'content-type: application/json'` returns schema-valid output from `qwen3:0.6b` (echo's binding for now) with realistic `latency_ms`.
- [ ] `just test-gpu` green on the 5070; `just test` green anywhere.
- [ ] Two concurrent requests: second's `meta.queued_ms` > 0 (observable via the sleepy-FakeLLM unit test; GPU spot-check optional).
- [ ] Unload endpoint verified: `/api/ps` empty afterward.

---

## Cycle T4 — `image-prompt` transform

**Goal:** the Brickfeed workload transform, exactly per DESIGN §7.1.

**In scope**
- `transforms/image_prompt.py`: schema, template, validators, binding verbatim from §7.1 (template refinement allowed only if fixture outputs fail shape — log any change in CYCLE-LOG and bump `version`).
- Fixtures: 5 sample news stories in `tests/fixtures/news/` (public-domain-safe: write 5 synthetic wire-style stories, 300–900 words, one multi-topic, one very long >3k est-tokens to exercise `lede_first_n`).
- Unit tests (FakeLLM): truncation triggers on the long fixture; validators catch a markdown-polluted fake response; word_range enforced.
- GPU test: all 5 fixtures through `qwen3:8b`, assert schema + validators pass, print outputs to console for human eyeball (test never asserts wording).

**Acceptance**
- [ ] `/v1/transform/image-prompt` on the 5070 produces sane one-line prompts for all 5 fixtures (human eyeball; paste them into CYCLE-LOG).
- [ ] Long fixture shows `truncated: true`.
- [ ] Non-gpu suite green anywhere; gpu suite green on the 5070.

---

## Cycle T5 — `cast-mentions` + `cast-canonicalize`

**Goal:** the two cast transforms per DESIGN §7.2–7.3, proven on real book prose.

**In scope**
- `transforms/cast_mentions.py` and `transforms/cast_canonicalize.py` verbatim from DESIGN (schemas, options schemas, templates, validators, bindings).
- Fixtures: `tests/fixtures/book/` — 4 logical-page-sized excerpts (500–800 words each) from *The Time Machine* (public domain; take from the Project Gutenberg #35 text, strip PG header/footer). Choose pages that include: (a) multi-character dialogue with descriptors, (b) a page with zero characters (pure description), (c) a first-introduction page, (d) a page using only pronouns/epithets for an established character.
- One canonicalize fixture: hand-assembled options payload for "the Time Traveller" with ~8 descriptor strings drawn from the excerpts.
- Unit tests (FakeLLM): options-schema enforcement (missing `descriptors` → 400); page-over-budget → 413 (budget is `reject` for cast-mentions); validators.
- GPU tests: mentions on all 4 excerpts — assert schema-valid, assert the zero-character page returns `mentions: []` **or** only `is_person: false` entries (loose assertion, log actual); canonicalize fixture — assert `one_line` ≤ 160 chars and `visual_description` sentence count 2–4 (split on `. ` heuristic, tolerant).

**Acceptance**
- [ ] GPU run outputs pasted into CYCLE-LOG; human-eyeball: descriptors are verbatim-ish quotes, not inventions; canonical description is paintable and era-plausible.
- [ ] All suites green in their environments.

---

## Cycle T6 — `scene-update` + `illustration-prompt`

**Goal:** the ledger and prompt transforms per DESIGN §7.4–7.5, including the sequential-threading pattern proven in a test.

**In scope**
- `transforms/scene_update.py` and `transforms/illustration_prompt.py` verbatim from DESIGN.
- Fixture: 3 *consecutive* Time Machine pages (extend the T5 fixture set), plus a hand-written `prior_ledger: null` start.
- Unit tests (FakeLLM): options schemas (`prior_ledger` accepts object and null; cast array shape); illustration validators catch medium-words ("watercolor") in a fake response; ledger output missing a required field → the schema catches it → retry path exercised.
- GPU tests: thread the 3 pages sequentially through `scene-update` (each call's `prior_ledger` = previous output); assert every output schema-valid, `location` non-empty, and `visual_salience` within [0,1]; then run `illustration-prompt` on the highest-salience page using the T5 canonical cast entry; assert schema + validators + `depicted ⊆ options.cast names or empty` (warn-only if not — log it, per DESIGN "warn not fail" posture on name sets: implement as a soft validator that records to meta, does not 422).
- Add the soft-validator mechanism if not already present: validators may return `warn:<reason>` which lands in `meta.warnings[]` without failing the request. Small pipeline extension; unit test it.

**Acceptance**
- [ ] Sequential GPU threading run logged in CYCLE-LOG with all 3 ledgers and the final prompt; human-eyeball: ledger carries location correctly across pages that don't move, beat sentences are concrete.
- [ ] `meta.warnings` mechanism tested and documented in README's API notes.
- [ ] All suites green.

---

## Cycle T7 — Ops hardening

**Goal:** the service is deployable and pleasant to operate.

**In scope**
- `GET /v1/transforms` per DESIGN §4 (serialize registry; schemas included).
- Auth per ADR-0003: dependency checking `X-Transform-Key` when `TRANSFORM_API_KEY` set; `/health` exempt; 401 body matches error format; tests for on/off/wrong-key.
- Structured logging per DESIGN §9: one JSON line per request with all listed fields; `X-Request-Id` response header; request id generated per request (uuid4 hex short).
- `deploy/text-transform-service.service` systemd unit (DESIGN §9 verbatim, path-adjusted) + `deploy/README.md` install steps (rsync to `/opt`, `uv sync`, enable unit).
- README completed: purpose, API summary table, error taxonomy, config table, curl examples for every endpoint, "adding a transform" recipe (the 8-step checklist: module, schema, template, validators, register, fixtures, unit tests, gpu test).
- `GET /v1/transforms` and unload endpoint respect auth.

**Acceptance**
- [ ] With `TRANSFORM_API_KEY=secret`: missing header → 401; correct header → 200; `/health` open.
- [ ] Log lines are valid JSON (test captures and parses one).
- [ ] Human: service installed under systemd on the 5070, survives reboot, `/health` ok. (Executor prepares; human runs the install and confirms in the dispatch thread.)

---

## Cycle T8 — Brickfeed bench harness (DEFERRED — build only when explicitly dispatched)

Per DESIGN §11 / v1 §9: `bench/` CLI that takes ~30 story files, runs both providers (local via this service; Haiku via Brickfeed's existing provider code or a thin re-implementation reading `ANTHROPIC_API_KEY`), emits paired prompts as JSONL + an HTML side-by-side sheet; image-set generation is manual/scripted separately. Pass criteria per v1 §9. Not scheduled; Scriptorium does not depend on it.

---

## Cycle T9 — Brickfeed `story-cover`; `opinion-gate` HELD (SHIPPED 2026-07-13)

First of the Brickfeed-requested transforms from `docs/requests/brickfeed-2026-07.md` (copied in pre-dispatch, provenance `brickfeed@40acb90`). Dispatched as `story-cover` + `opinion-gate`; only `story-cover` shipped.

**In scope (done):** `src/tts/transforms/story_cover.py` (`story-cover`, v0.1.0, `qwen3.5:9b`) — five-field cover bundle (headline/description/imagePrompt/category-enum/caption), `options_schema {}`, `input_budget=1200` truncate/head, subject-neutral validators mirroring `image-prompt`; 5 synthetic fixtures under `tests/fixtures/story_cover/`; 7 FakeLLM unit tests; a T9 GPU section. Records: this section, the §0.2 index row, CYCLE-LOG T9, NOTES-FOR-NEXT-CYCLES.

**Out of scope / not built:** `opinion-gate` — **HELD**. Its requested behavior (fail-closed, safety-load-bearing exclusion of tragedy/violence/death) is "safety-relevant classification", excluded by DESIGN §1 line 9. Building it amends the charter → a product-owner ADR call, escalated in the plan-mode decision + CYCLE-LOG + NOTES, not an executor build. Also out: T10's pair, provider/bench code, edits to existing transforms.

**Reconciliations (binding contract, not the request doc):** `category` gains `"type":"string"`; `imagePrompt`/`caption` held subject-neutral per ADR-0004 (request's "cartoon"/"playful/cartoonish" style stripped; toy-brick stays caller-side); `word_range(8,60)` on imagePrompt; truncation is a structural no-op on the single-paragraph input (never rejects). Full detail in the module docstring + CYCLE-LOG.

**Acceptance**
- [x] `make lint` clean; `make test` → 127 passed (120 prior + 7 new), 8 gpu deselected.
- [x] `make test-gpu` on the 5070 green (`qwen3.5:9b`); story-cover outputs pasted into CYCLE-LOG for the eyeball (subject-neutral, no style leak).
- [x] `opinion-gate` hold + charter escalation recorded (CYCLE-LOG + NOTES); no `opinion_gate.py` created.

---

## Appendix A — Kickoff prompt template (for cycles T2+)

Fill and dispatch one per cycle. T1's concrete kickoff is provided separately as `cc-kickoff-tts-cycle-01.md`.

```
You are executing cycle {ID} of text-transform-service.

Read, in order, before planning:
1. system-overview.md (repo root)
2. text-transform-service-DESIGN.md — especially §{relevant}
3. text-transform-service-BUILD-PLAN.md — §0 discipline, then the Cycle {ID} section
4. CYCLE-LOG.md and NOTES-FOR-NEXT-CYCLES.md for context from prior cycles

Then enter plan mode: restate scope, files, tests, ambiguities. Wait for approval
{or: proceed if unambiguous — dispatcher's choice}.

Hard rules for this cycle:
- Scope fence: only the Cycle {ID} "In scope" list. Discoveries → NOTES-FOR-NEXT-CYCLES.md.
- Definition of done: BUILD-PLAN §0 item 5, plus the Cycle {ID} acceptance checklist.
- Never assert exact LLM wording in tests.
- GPU-marked tests: write them; run them only if you are on the 5070 (check
  nvidia-smi); otherwise state they need a manual run and list the command.

Deliverables: code + tests + docs per scope; CYCLE-LOG.md entry; a closing summary
listing every acceptance box as checked/blocked-with-reason.
```
