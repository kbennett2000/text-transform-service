# Cycle Log

## T13 — `opinion-gate` batch completeness: q8_0 KV-cache host binding (2026-07-14)

Mini-cycle, follow-on to T12. T12's `num_ctx` fix removed the loud **422** at 34-candidate volume,
but live verification exposed a *deeper, silent* failure: at the computed `num_ctx=14144`,
`qwen3.5:9b` with the default **f16** KV cache under flash attention **closes the verdict array
early** under temp 0 — e.g. **27/34** verdicts, `truncated=0`, HTTP **200**, no error. The caller's
fail-closed rule then maps the missing tail ids to *exclude*, so batches are silently over-excluded.
This is **worse than the 422** (loud → silent) and it even regressed the previously-working
21-batch (21/21 → 17/21 at 14144). **Root cause (confirmed live): the f16 KV cache, not flash
attention.** Fix: **`OLLAMA_KV_CACHE_TYPE=q8_0`** on the Ollama daemon (a host binding, not TTS
code). opinion-gate stays **0.3.0** — no transform contract or code changed. **Scope: the host-level
Ollama binding + findings docs; opinion-gate version unchanged. No schema shapes, no other transform
contracts, no error-code changes.**

**Product-owner ruling (pre-decided branches).** Run the KV-quant test at 14336 ctx against a hard
pass bar (100% GPU + ≥2 GB headroom; 21/34/60 complete with id-set equality; 34-batch ≤ ~90 s; 3
consecutive stable runs). PASS → host binding; FAIL → cap `num_ctx=4096` + caller chunking ≤15.
Per-story calls **rejected** (latency × volume unacceptable for a cron gate). **Result: PASS.**

**Diagnosis (Phase A, live on the 5070, throwaway Ollama on an alt port, driven through TTS's own
`OllamaClient` for a faithful prompt/schema/`num_ctx`).** Four configs at `num_ctx=14336`:

| config | 21 | 34 | 60 | GPU / VRAM | 34-latency |
|---|---|---|---|---|---|
| flash **on**, **f16** KV (= prod today) | 17/21 ✗ | 27/34 ✗ | tail-drop ✗ | 100% GPU | ~15–28 s |
| flash **off**, f16 KV | 21/21 ✓ | complete | complete | **74% CPU** (12/34 layers) | **~500 s** ✗ |
| flash **off**, q8_0 KV | — | — | — | **segfault on load** | — |
| flash **on**, **q8_0** KV | **21/21** | **34/34** | **60/60** | **100% GPU, ~4 GB free** | **~22 s** |

The two constraints that force the answer:
1. **`OLLAMA_FLASH_ATTENTION=1` is mandatory** — `llama_init_from_model: V cache quantization
   requires flash_attn` → segfault. "flash-off + q8_0" (the ruling's literal config) is physically
   impossible; q8_0 forces flash on. **Deviation from the ruling's wording, forced by llama.cpp;
   the ruling's KV-quant *intent* and its pass bar are fully satisfied.**
2. **flash-off is a dead end on this 12 GB card** — the non-flash attention compute buffer at 14336
   ctx doesn't fit, so Ollama offloads 22/34 layers to CPU (74% CPU, ~500 s). This is the
   "508 s" regime from the prior investigation; it is not VRAM-of-the-KV-cache but the attention
   scratch buffer.

So the tail-drop was **the f16 KV cache under flash attention**, not flash attention itself. Same
flash-on setting, KV dtype f16→q8_0, and completeness goes 27/34 → 34/34.

**3× stability (the flakiness guard — prior GPU suite passed then started tail-dropping with no
config change).** flash-on + q8_0, three consecutive runs, **byte-identical**:

```
run1: [21] 21/21 20.1s  [34] 34/34 21.8s  [60] 60/60 37.2s
run2: [21] 21/21 13.6s  [34] 34/34 22.1s  [60] 60/60 37.4s
run3: [21] 21/21 13.5s  [34] 34/34 22.0s  [60] 60/60 37.1s
tallies identical every run: 21→{elig15,excl6} 34→{elig25,excl9} 60→{elig43,excl17}
```

**VRAM (hard gate).** flash-on + q8_0 at 14336: `ollama ps` → **5.6 GB, 100% GPU, CONTEXT 14336**;
KV cache 238 MiB (q8_0, vs 448 MiB f16). nvidia-smi during active generation: 7.8 GB used / 12 GB,
**3.9 GB free** — above the 2 GB headroom bar even mid-inference (~4.4 GB idle).

**Shipped**
- `deploy/ollama.service.d/flash-attn.conf` — systemd drop-in: `OLLAMA_FLASH_ATTENTION=1` +
  `OLLAMA_KV_CACHE_TYPE=q8_0`, with the rationale + install/verify commands inline. Applied by the
  human (sudo — same boundary as `deploy/redeploy.sh`); TTS cannot set these (Ollama 0.30.7 ignores
  per-request flash/KV options — the runner reads daemon-level env once per model load).
- `deploy/README.md` — new **§3a** installs the drop-in as a host-level prerequisite (ordered like
  the `.env` step), with the mandatory-flash caveat.
- `docs/models.md` — new "HOST BINDING — q8_0 KV cache required" section: the config, the 4-row
  evidence table, the two hard constraints, host-wide-scope safety note, and the
  "per-request flash_attn is ignored" subsection explaining why it must be host config.
- `NOTES-FOR-NEXT-CYCLES.md` — T13 findings: q8_0 is a host binding any large-`input_budget`
  transform inherits; per-request flash/KV ignored; flash-off is a dead end; per-story rejected.
- `src/tts/transforms/opinion_gate.py` — docstring T13 note (no code change; version stays 0.3.0).
- `tests/test_gpu.py` — the T12 volume test docstring gains a T13 note (id-set equality at volume
  depends on the host q8_0 binding; check it first if the test fails). No assertion change.

**Verification**
- `make lint` clean; `make test` → non-GPU suite unchanged and green (no code paths changed).
- Phase A live gate (above): flash-on + q8_0, 3× stable, every pass-bar criterion met.
- **Pending human (sudo, like the T12 redeploy):** apply the drop-in to the prod `ollama.service`,
  `systemctl restart ollama`, then re-run `make test-gpu` 3× against the reconfigured prod daemon
  (the T12 volume test asserts id-set equality at 14144 — it will pass with the binding, tail-drop
  without) and POST the 34-fixture through the deployed service → 200 complete. Recorded here when
  done. The Phase A gate above is the same config verified 3× against a throwaway daemon.

**Deviations from the plan / ruling.**
1. **Winning config is flash-*on* + q8_0, not the ruling's flash-*off* + q8_0** — flash-off + q8_0
   segfaults (`V cache quantization requires flash_attn`), so the literal config cannot run. The
   test found the config that satisfies every pass-bar criterion; it is a q8_0 KV binding exactly as
   the ruling intended, with flash pinned on (mandatory). This also corrects the prior root-cause
   theory (flash attention was blamed; the f16 KV cache was the culprit).
2. **Prod-daemon 3× GPU re-verification is post-apply (human sudo)**, not done in-cycle — the box
   has no passwordless sudo and the executor does not reconfigure the prod daemon (same boundary as
   redeploy). The equivalent 3× verification was done against a throwaway daemon with the identical
   binding.

## T12 — registry-wide `num_ctx` fix for large-batch output truncation (2026-07-13)

Mini-cycle. Brickfeed's live verification of `opinion-gate` v0.2.0 failed deterministically at
34-candidate volume (~16.5 KB, *under* the 8000-token budget): HTTP **422 `invalid JSON`**,
3/3 failures at ~900 ms, while a 21-candidate batch (~12 KB) succeeded in ~18 s. **Root cause
(confirmed live): TTS never set Ollama's `num_ctx`, so the runtime default of 4096 tokens applied.**
A large-batch prompt fills the window and starves generation; Ollama truncates the output mid-JSON
(`slot release: … truncated = 1`) → the parse fails. This was **latent in every transform** — it
only surfaced where budgets grew. **Scope: the `num_ctx` mechanism + opinion-gate version +
observability snippet. No schema shapes, no other transform contracts, no error-code changes.**
opinion-gate bumped **0.2.0 → 0.3.0**.

**Diagnosis (Phase 1, live on the 5070).** Ollama server log, smoking gun, on the 34-candidate
prompt at the default context:
```
llama_context: n_ctx_seq (4096) < n_ctx_train (262144)
slot update_slots: id 0 | task 0 | new prompt, n_ctx_slot = 4096, n_keep = 4, task.n_tokens = 3952
slot      release: id 0 | task 0 | stop processing: n_tokens = 4095, truncated = 1
```
The 3952-token prompt left ~144 tokens for output → ~2 verdicts → mid-string cutoff. With the fix
(`num_ctx=14144`) the identical request returns **200** with all verdicts, single attempt.

**Shipped**
- `src/tts/registry.py` — `Transform` gains `num_ctx: int | None = None`; a `__post_init__`
  (frozen-safe via `object.__setattr__`) computes it as **`input_budget + num_predict + 1024`**
  headroom when unset, overridable per transform. Applies registry-wide (every transform now
  carries a correct, concrete ctx — e.g. echo 4536, image-prompt 4184, opinion-gate 14144).
- `src/tts/pipeline.py` — threads `transform.num_ctx` into the per-attempt LLM `params`. **Plus a
  permanent observability improvement (kept regardless of root cause):** on total validation
  failure the 422 `detail` now carries a bounded `raw_snippet` (last raw output, ≤300 chars) and a
  `logger.debug` line — additive to `detail.reasons`, no error-code change. This is what made the
  truncation visible (empty/garbage vs. a real verdict payload).
- `src/tts/llm.py` — `OllamaClient` adds `num_ctx` to the Ollama `options` sub-object; protocol +
  client docstrings updated.
- `src/tts/transforms/opinion_gate.py` — `version` **0.2.0 → 0.3.0**; docstring v0.3.0 change note.
  No field on the transform itself changed (the fix is the registry-wide mechanism + the computed
  default landing at 14144).
- `tests/fixtures/opinion_gate/07_volume_batch_34.txt` (34 candidates, ~15.6 KB, all distinct) and
  `08_volume_batch_60.txt` (60 candidates, ~5.8k est-tokens, large near-budget) — the batches that
  truncated pre-fix. Distinct stories (identical repeats made the model degenerate — a fixture
  artifact, not a service bug).
- `tests/test_registry.py` (+2: computed default; override respected), `tests/test_pipeline.py`
  (+4: num_ctx threaded default+override; 422 carries `raw_snippet`; snippet bounded to 300),
  `tests/test_ollama_client.py` (`options` now asserts `num_ctx`), `tests/test_opinion_gate.py`
  (v0.3.0, `num_ctx == 14144`), `tests/test_gpu.py` (T12 parametrized volume test at 34 and 60;
  the two direct-`chat` tests pass `num_ctx`; curated-5 glob excludes the volume fixtures).
- `docs/requests/brickfeed-2026-07-RESPONSE.md` §2 — v0.3.0 note: within-budget large batches now
  succeed; no API change; keep chunking beyond 100 candidates / 8000 est-tokens.

**VRAM (hard gate, verified live).** opinion-gate at `num_ctx=14144` on `qwen3.5:9b`: `ollama ps`
shows **5.7 GB, 100% GPU, CONTEXT 14144** (KV cache fully on-card, no CPU offload). nvidia-smi:
8.2 GB used / 12 GB, **3.5 GB free** even with ~2.5 GB held by another process; >6 GB headroom when
the card is otherwise idle. GPU-phase exclusivity (system-overview §5) means renders never run
concurrently. Fits with margin.

**Verification**
- `make lint` clean.
- `make test` → **146 passed** (140 prior + 6 new), 13 gpu deselected.
- `make test-gpu` on the 5070 (`qwen3.5:9b`) → **13 passed** (11 prior + 2 new volume params).
  All volume batches: single attempt, **id-set equality held**, every designed tragedy excluded,
  no quality drift across the list. Latencies: 21→14.0 s, 34→28.4 s, 60→50.0 s.

```
=== T12 opinion-gate GPU verdict table @ 34-candidate volume (qwen3.5:9b, num_ctx=14144) ===
latency_ms=28380 attempts=1 n=34 excluded=9 tragedies=8/8 caught
  s03=excluded (fatal crash killing student athletes)   s06=excluded (fatal warehouse fire, two killed)
  s09=excluded (flash flooding, deaths/missing)         s12=excluded (earthquake, casualties)
  s15=excluded (ferry capsize, dozens missing)          s18=excluded (gas explosion kills a family)
  s21=excluded (bus off mountain road, fatalities)      s24=excluded (mine collapse, deaths)
  s33=excluded (borderline — one conservative extra exclusion; fail-closed direction)
  (remaining 25 eligible — pumpkins, therapy dogs, marching band, tutoring, etc.)

=== T12 opinion-gate GPU verdict table @ 60-candidate volume (qwen3.5:9b, num_ctx=14144) ===
latency_ms=49969 attempts=1 n=60 excluded=17 tragedies=16/16 caught
  16/16 designed tragedies excluded (s03,06,09,12,15,18,21,24,27,30,33,36,39,42,45,48);
  s58 one conservative extra exclusion; all others eligible. id-set equality held.
```

**Live verification (dev instance, real model).** Before merge/redeploy, the fix was exercised on
a local dev instance (real `qwen3.5:9b`, port 8713) against the 34-candidate fixture:
**422 `invalid JSON` (v0.2.0-equivalent, default ctx) → 200 with 34 verdicts, v0.3.0, single
attempt** once `num_ctx` was threaded. The `/opt` systemd redeploy (`deploy/redeploy.sh`, human
sudo) and its 422→200 against the deployed service is the post-merge step, recorded below when done.

**Deviations from the plan.** None material. (1) The reproduced signature was mid-output truncation
(`Unterminated string`) rather than the production `char 1` fast-fail — the same root cause in an
adjacent regime (my fixture is marginally smaller than the production 16.5 KB, so the prompt fit
the window but starved the output instead of being truncated itself). Both are context exhaustion;
the Ollama log confirms it. (2) The first fixture draft cycled a small story pool, and identical
repeats late in the context made the model produce weird tail verdicts — rewritten with fully
distinct stories; the fix itself was never in question (id-set equality held throughout).

## T11 — `opinion-gate` input budget fixed for real batch volumes (2026-07-13)

Mini-cycle. Brickfeed's live verification of `opinion-gate` 413'd (`over_budget`) on a routine
21-candidate batch (11,976 B vs `input_budget: 1600`). The 0.1.0 contract was sized for single
stories, but the task is batch-shaped by design (`verdicts` `maxItems: 100`). **Scope: opinion-gate
only — no other transform, no schema shape changes.** Bumped to **v0.2.0**.

**Shipped**
- `src/tts/transforms/opinion_gate.py` — `input_budget` **1600 → 8000** est-tokens; `version`
  **0.1.0 → 0.2.0**; docstring gains a v0.2.0 change note. `over_budget="reject"` **unchanged**
  (decided): truncating a batch would drop trailing candidates, which the caller's missing-id rule
  then excludes — quiet starvation of the tail; reject is the honest failure.
- `tests/fixtures/opinion_gate/06_realistic_batch.txt` — synthetic realistic 21-candidate batch
  (~12 KB / ~2.5k est-tokens; the shape that 413'd at 1600, now under 8000). Mixed subject matter:
  15 lighthearted + 6 genuine tragedy/disaster (crash, fire, flood, earthquake, ferry, explosion).
- `tests/test_opinion_gate.py` — binding asserts updated (`version 0.2.0`, `input_budget 8000`);
  happy-path `transform_version 0.2.0`; **new** `test_realistic_batch_passes_budget` (21-candidate
  fixture passes budget, reaches generation, 21 verdicts id-set equal); over-budget 413 test
  resized 60→100 padded candidates so it still clears the raised 8000 budget (~11.6k est-tokens).
- `tests/test_gpu.py` — **new** `test_opinion_gate_realistic_batch_at_volume` (full run on
  `qwen3.5:9b`, schema-valid + id-set equality at 21-candidate volume, prints the verdict table);
  the curated-5 verdict test now excludes the batch fixture by name (stays exactly 5).
- `docs/requests/brickfeed-2026-07-RESPONSE.md` §2 — budget updated 1600→8000; added consumer note
  that batches approaching ~100 candidates or ~8000 est-tokens must be chunked caller-side (TTS
  413s rather than judge a truncated batch).

**Deviation from the decided fix (approved mid-cycle).** The decided fix was `input_budget` only.
GPU verification exposed that `num_predict=1024` cannot emit a verdict per candidate for a real
batch — the output JSON truncated mid-string → **422 after retries** (input fit, output did not).
The cycle's own acceptance (gpu full run at volume + live 200 on the 21-batch) is unreachable
without also raising the output ceiling. With product-owner approval, **`num_predict` 1024 → 5120**,
sized to what the 8000 input budget admits (~66 candidates × ~70 out-tokens). It is a ceiling, not
a fixed cost — small batches stop at the natural JSON end, so only large batches are affected.
opinion-gate-only, no schema change; folded into v0.2.0.

**Verification**
- `make lint` clean.
- `make test` → **140 passed** (139 prior + 1 new unit test), 11 gpu deselected.
- `make test-gpu` on the 5070 (Ollama, `qwen3.5:9b`) → **11 passed** (10 prior + 1 new volume
  test). The 21-candidate batch returned 21/21 verdicts, **id-set equality held**, single attempt,
  latency ~17.4 s. **No quality drift across the long list**: all 6 tragedy/disaster items excluded,
  the 15 lighthearted items eligible, coherent all the way to s21. Verdict table:

```
=== T11 opinion-gate GPU verdict table @ 21-candidate volume (qwen3.5:9b) ===
latency_ms=17441 attempts=1 n=21
  s01=eligible (record-breaking pumpkin / community fair)
  s02=eligible (kids reading to therapy dogs)
  s03=excluded (fatal interstate pileup, student deaths)
  s04=eligible (diner 75th anniversary)
  s05=eligible (amateur astronomer photographs comet)
  s06=excluded (fatal warehouse fire, two workers killed)
  s07=eligible (middle-schoolers' recycling robot)
  s08=eligible (cat elected honorary mayor)
  s09=excluded (flash flooding, multiple deaths, missing)
  s10=eligible (retiree knits sweaters for penguins)
  s11=eligible (marching band national championship)
  s12=excluded (earthquake levels district, casualties feared)
  s13=eligible (world-record longest baguette)
  s14=eligible (community garden / pollinator haven)
  s15=excluded (ferry capsizes, confirmed fatalities)
  s16=eligible (town clock finally fixed)
  s17=eligible (lost dog reunited with family)
  s18=excluded (chemical plant explosion, worker killed)
  s19=eligible (grandmother's chili cook-off win)
  s20=eligible (volunteers plant ten thousand trees)
  s21=eligible (kids' lemonade stand for children's hospital)
=== end T11 volume outputs ===
```

**Live verification (redeployed to `/opt` on G434, `qwen3.5:9b`)**
- `deploy/redeploy.sh` run by the human (sudo prompts on their terminal). `/v1/transforms` now
  lists **`opinion-gate 0.2.0`** (the seven others unchanged at 0.1.0); `/health` = `ok`.
- **Before/after on the reported batch:** the 21-candidate fixture (`input_tokens_est: 2517`)
  → `413 over_budget` on the live 0.1.0 deploy (`budget: 1600` — reproduces Brickfeed's report
  exactly), then → **`200`** on 0.2.0: 21/21 verdicts, id-set equality, `truncated: false`,
  1 attempt, ~18.2 s, 15 eligible / 6 excluded (the six tragedy/disaster items s03/06/09/12/15/18).
- **Ops note (no code impact):** first live curls returned `503 model_unavailable` at the client's
  hard 120 s timeout — a long-running ComfyUI had ballooned to ~5–7 GB VRAM, starving Ollama into a
  ~69% CPU load (~0.4 tok/s). Not a service fault. Cleared by unloading the model
  (`ollama stop`) and freeing VRAM (ComfyUI queue-idle → stop → curl → relaunch, all as `kb`, no
  sudo); with the model `100% GPU` the batch runs ~18 s. Recorded in the deploy-host memory.

**Deviations:** the `num_predict` bump above (approved). No schema shape changes. No other
transform touched. README unchanged (it does not surface opinion-gate's version/budget).

## Ops — redeploy after T10; live registry current, `redeploy.sh` added (2026-07-13)

The deployed `/opt/text-transform-service` was stale (pre-T9/T10): the live registry served only
`cast-canonicalize`, `cast-mentions`, `illustration-prompt`, `image-prompt`, `scene-update`.
Redeployed current merged `master` (`988a109`, PR #11) per `deploy/README.md` — rsync to `/opt`,
`uv sync` as the service user (`kb`, never root), `systemctl restart`. **Verified live**: unit
active; `/v1/transforms` now lists all eight — the five above plus `opinion-gate`,
`opinion-image-brief`, `story-cover` (`echo` correctly absent, dev-gated). `/health` = `degraded`
(`ollama_reachable:false`) — Ollama not running on the box; documented-correct (never 500s), not a
redeploy fault; `→ ok` awaits a human starting Ollama.

**Added** `deploy/redeploy.sh` — idempotent one-command redeploy (pull → rsync → `uv sync` →
restart → print live transform list). Refuses to run as root (guards the `uv sync` 203/EXEC trap
from README §2); sudo is invoked internally for rsync + restart. One-line pointer added to
`deploy/README.md`. No transform code or templates changed; no version bumps.

## T10 — Brickfeed `opinion-gate` (under ADR-0007) + `opinion-image-brief` (2026-07-14)

Second half of the Brickfeed request pair (`docs/requests/brickfeed-2026-07.md` §2 + §4).
`opinion-gate` — HELD in T9 as out of the DESIGN §1 charter — is now **admitted under
ADR-0007** and shipped; `opinion-image-brief` (always in-charter, ADR-0004) shipped; the four
requests are formally dispositioned. **`opinion-piece` stays HELD** (long-form voiced
generation, §1) by product-owner decision — not built, no module, no fixtures.

**ADR-0007 (transcribed, not re-argued).** `docs/adr/0007-safety-classification-exception.md`
— product-owner ruling: §1's blanket "no safety-relevant classification" becomes a
*conditional* exclusion. A safety classifier may register iff (1) its verdict is a closed enum
including an explicit `uncertain` (no free text drives the decision); (2) the module documents
the caller's fail-closed obligation (every error + every `uncertain` = the safe outcome; TTS
stays fail-loud, no fallback); (3) scope is editorial gating of machine-selected public content
with human audit — not user-generated moderation. `opinion-gate` satisfies all three.

**Shipped** — two new production transforms (net-new; module docstrings + this entry are the
binding contracts).
- `src/tts/transforms/opinion_gate.py` — `build_opinion_gate()` [`opinion-gate`, v0.1.0,
  `qwen3.5:9b`]. Input: JSON array `[{id,title,summary}]`; output: `verdicts[]` of
  `{id, verdict∈{eligible,excluded,uncertain}, reason(1–200)}`, `maxItems:100`. `options_schema`
  `{}`; `input_budget=1600`, **`over_budget=reject`→413**; temp 0.0, num_predict 1024.
  Validators: `no_empty_strings("verdicts[].id")`, `no_empty_strings("verdicts[].reason")`.
  Docstring carries the caller fail-closed obligation verbatim (ADR-0007 condition 2).
- `src/tts/transforms/opinion_image_brief.py` — `build_opinion_image_brief()`
  [`opinion-image-brief`, v0.1.0, `qwen3.5:9b`]. Output: `{imagePrompt(30–400),
  caption(15–160)}`, subject-only. `options_schema` `{}`; `input_budget=3000`,
  `over_budget=truncate`/`head`; temp 0.4, num_predict 256. Reuses T9's subject-neutral
  validator set (`banned_substrings` + `word_range("imagePrompt", 8, 60)`); template forbids
  style/medium words **and** depicting the author / the act of writing.
- `docs/requests/brickfeed-2026-07-RESPONSE.md` — disposition of all four requests (the contract
  the Brickfeed provider cycle reads): tasks 1/2/4 routable, task 3 held; the opinion-gate
  fail-closed contract is stated caller-facing there.
- `tests/fixtures/opinion_gate/` — 5 JSON-array batches (`01_mixed`/`02_mixed` = request Ex-A/B;
  `03_tragedy` all death/disaster; `04_lighthearted` all harmless; `05_ambiguous` one borderline).
- `tests/fixtures/opinion_image_brief/` — 5 synthetic finished-piece + subject-context inputs
  (bike-lanes columnist + ladder letter = request Ex-A/B; pumpkin/star/marathon). Inputs only —
  `opinion-piece` is NOT built; bodies are short hand-written stand-ins.
- `tests/test_opinion_gate.py` (+7 FakeLLM): binding/shape (enum includes `uncertain`,
  `over_budget=reject`); happy-path mixed verdicts; **`uncertain` accepted**; over-budget →413
  with `fake.calls == []`; out-of-enum verdict →422; reason >200 →422; whitespace-only reason
  →422 (`no_empty_strings` catch).
- `tests/test_opinion_image_brief.py` (+5 FakeLLM): binding/shape; happy-path (2 keys);
  imagePrompt banned-substring →422; >60-word →422; caption below `minLength 15` →422.
- `tests/test_gpu.py` — `# --- T10 ---` section (both transforms, all fixtures, `qwen3.5:9b`);
  gate asserts schema/enum + **id-set equality** (one verdict per id) + safe-set membership
  {excluded,uncertain} on the tragedy + ambiguous fixtures (never a single verdict);
  image-brief asserts shape only. **Authorized stability fix:** T5's flaky
  `cast_canonicalize` sentence-count assertion loosened from `2 ≤ n ≤ 4` to `≥ 1` (per NOTES).

**Reconciled contracts vs. requested** (recorded in the module docstrings):
- *opinion-gate:* (1) verdict enum gains a third value `uncertain` (ADR-0007 cond. 1) — TTS
  emits it honestly, the caller maps `uncertain`/errors/missing-ids → exclude; (2) `verdict`
  gets `"type":"string"` alongside `enum`; (3) `verdicts` `maxItems:100`, `reason` `minLength:1`
  (T9 NOTES); (4) `over_budget=reject` kept from the request.
- *opinion-image-brief:* (1) subject-neutral (ADR-0004) — comedic scene fine, no style/medium
  words, and depict the subject not the author; (2) `imagePrompt` bound `word_range(8,60)`.

**Verification**
- `make lint` clean (no `# noqa`).
- `make test` → **139 passed** (127 prior + 12 new), 10 gpu deselected.
- `make test-gpu` on the 5070 (Ollama 0.30.7, `qwen3.5:9b`) → **10 passed** (full suite green;
  the loosened T5 test passed). Outputs below.

**GPU outputs — opinion-gate verdict table (`qwen3.5:9b`; verdict sanity, all `attempts=1`):**
```
[01_mixed]        a1=eligible (harmless giant-pumpkin achievement); b2=excluded (centers fatal casualties/death)
[02_mixed]        c3=eligible (mayor napping, harmless); d4=excluded (centers storm victims/disaster relief)
[03_tragedy]      t1=excluded (deadly wildfire, multiple deaths); t2=excluded (explosion, employee fatalities); t3=excluded (ferry capsize, drownings)
[04_lighthearted] h1=eligible (cat plaque); h2=eligible (record baguette); h3=eligible (ducklings crossing)
[05_ambiguous]    m1=excluded (death/passing of a beloved animal — tragic, not lighthearted)
```
Every death/disaster story excluded; every harmless story eligible; the borderline elephant-death
landed `excluded` (in the safe set). No id dropped or duplicated. `uncertain` did not surface
naturally here (the model was confident on these fixtures), but the enum accepts it and the unit
suite proves it is honored — the caller treats it as exclude regardless.

**GPU outputs — opinion-image-brief on `qwen3.5:9b` (subject-only, depicts subject not author):**
```
[01_bike_lanes]   imagePrompt: A wide downtown street where fresh green painted lanes form a connected grid for cyclists, while pedestrians are pushed to the narrow margins of the road as riders glide past with smug expressions.
                  caption: Protected bike lanes push pedestrians aside in favor of gleaming two-wheeled cruisers.
[02_ladder_letter] imagePrompt: A cluttered suburban porch where a neighbor returns a wooden ladder missing its bottom rung to an expectant homeowner who stands beside the gap.
                  caption: The borrowed ladder arrives back one step shorter than it left.
[03_pumpkin]      imagePrompt: A massive two-thousand-pound pumpkin with a bloated surface sits on a flatbed trailer in an outdoor fairground setting.
                  caption: Sheer size is mistaken for character as the giant gourd arrives at the fair.
[04_star]         imagePrompt: A distant star rotates at an impossibly rapid pace in deep space, blurring its surface into a dizzying whirl while astronomers observe from Earth with instruments.
                  caption: An unusually fast-spinning distant star whirs giddily through the cosmos.
[05_marathon]     imagePrompt: Thousands of runners wearing numbered bibs finish a marathon in intense summer heat along a crowded downtown street lined with cheering spectators.
                  caption: Runners complete the city annual marathon under record-breaking summer temperatures.
```
Subject-only — scenes depict the story subject (lanes, ladder, pumpkin, star, runners), never the
columnist or the act of writing; no style/medium/camera words leaked.

**Template change:** two new transforms, both v0.1.0.

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
