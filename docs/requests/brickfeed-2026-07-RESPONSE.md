<!--
Disposition of the Brickfeed transform request set (`brickfeed-2026-07.md`, provenance
brickfeed@40acb90), authored by the TTS side across cycles T9–T10 (2026-07-13 / 2026-07-14).

This is the contract the Brickfeed *provider* cycle reads to decide what to route to TTS. Where
it and the original request differ, THIS doc plus each transform's module docstring + CYCLE-LOG
entry are binding — not the request.
-->

# Brickfeed request set — disposition (TTS response)

Brickfeed asked for four gap transforms. **Three are shipped and routable; one is held.** The
provider cycle can migrate tasks 1, 2, and 4 off the incumbent Claude/Haiku provider now; task 3
stays on the incumbent by design.

| # | Transform | Status | Built | Route to TTS? |
|---|---|---|---|---|
| 1 | `story-cover` | **SHIPPED** | T9 | Yes |
| 2 | `opinion-gate` | **SHIPPED** (under ADR-0007) | T10 | Yes — **with the fail-closed contract below** |
| 3 | `opinion-piece` | **HELD** (out of charter) | — | No — stays on incumbent |
| 4 | `opinion-image-brief` | **SHIPPED** | T10 | Yes |

All shipped transforms bind `qwen3.5:9b`, expose `options_schema {}`, and follow the standard
success/error envelopes (`docs/ai-reference.md`). The subject-neutral image rules (ADR-0004)
apply to every `imagePrompt`/`caption`: the model emits a neutral scene; **Brickfeed applies its
toy-brick styling caller-side.** A style/medium/camera/brand word in an image field is drift and
fails as `422 validation_failed`.

## 1. `story-cover` — SHIPPED (T9)

Five-field cover bundle (`headline`, `description`, `imagePrompt`, `category`, `caption`),
`input_budget=1200`, `over_budget=truncate`. Reconciliations (category gains `type:string`;
`imagePrompt`/`caption` held subject-neutral; `imagePrompt` bound `word_range(8,60)`; truncation
is a structural no-op on the single-paragraph input) are recorded in
`src/tts/transforms/story_cover.py` and the T9 CYCLE-LOG entry — read those as binding.

## 2. `opinion-gate` — SHIPPED (T10), admitted under ADR-0007

This is a safety-relevant classifier, which DESIGN §1 excludes by default. It was HELD in T9 and
is now admitted under **ADR-0007** (`docs/adr/0007-safety-classification-exception.md`), which
makes that exclusion conditional. The shipped contract:

- **Verdict enum is three-valued: `eligible` | `excluded` | `uncertain`.** The request listed
  only `eligible`/`excluded` and baked "if uncertain, exclude" into the prompt. TTS instead emits
  `uncertain` **honestly** — the service is fail-loud and never substitutes a default verdict.
- **`verdict` is the sole decision field.** `reason` (1–200 chars) is explanatory only; do not
  drive any decision from it.
- **`over_budget=reject` → 413.** A candidate list over `input_budget=1600` is rejected whole,
  never silently truncated (truncation would drop stories from classification).
- **Bounds:** `verdicts` is capped at `maxItems: 100`; `reason` has `minLength: 1`.

### ⚠️ Caller fail-closed obligation (REQUIRED — this is the safety contract)

TTS implements **no** fallback. The Brickfeed caller MUST treat all of the following as the safe
outcome — **exclude the story from satire**:

1. any transport/validation error (any 4xx/5xx from the service);
2. any `uncertain` verdict;
3. any input `id` that is **missing** from the response or appears **more than once**.

TTS guarantees one verdict per id when it succeeds, but id-completeness is not something the
service can enforce against your input on your behalf — the fail-closed rule above closes that
gap. This mirrors the incumbent's "any malformed response = all stories excluded" posture, made
explicit. ADR-0007 also expects **periodic human audit** of the gate's decisions on the consumer
side; the exception does not cover un-audited gating.

## 3. `opinion-piece` — HELD (out of charter)

**Not built. Stays on Brickfeed's incumbent provider.** The requested `body` is long-form prose
(up to ~2000 words) in a persona's satirical *voice* — i.e. long-form **voiced generation**,
which DESIGN §1 excludes ("not for long-form voiced generation"). ADR-0007 amended only the
*safety-classification* exclusion; it does not touch the long-form-voiced exclusion, and the
product owner has not authorized voiced generation on TTS. Revisiting this requires (a) a bench
demonstrating the local model produces acceptable voiced output and (b) an explicit product
decision / new ADR — not an executor call. Until then, route task 3 to the incumbent.

## 4. `opinion-image-brief` — SHIPPED (T10)

Two-field brief (`imagePrompt` 30–400, `caption` 15–160), `input_budget=3000`,
`over_budget=truncate` (`head` — keeps the leading piece; trimming the tail is harmless).
Subject-neutral (ADR-0004), reusing `story-cover`'s validator set. The template additionally
enforces the request's intent: **depict the story/letter subject, never the author or the act of
writing/publishing** (no writers, desks, typewriters, newspapers, or bylines in the scene).
Reconciliations are recorded in `src/tts/transforms/opinion_image_brief.py` and the T10
CYCLE-LOG entry.

---

**Bottom line:** after T10, TTS owes Brickfeed nothing further. The provider cycle is unblocked
with 3 of 4 tasks routable to TTS and task 3 (`opinion-piece`) staying on the incumbent by design.
