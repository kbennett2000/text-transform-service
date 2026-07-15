# text-transform-service — Design Document v2

**Status:** Approved for build — supersedes the 2026-07-13 v1 draft
**Date:** 2026-07-13
**Owner:** Kris Bennett / Twelve Rocks LLC

**Changes from v1:** (a) a second consumer, Scriptorium, adds four transforms; (b) output is formalized as schema-constrained JSON, not bare text; (c) transforms may declare a structured `options` input schema; (d) the v1 open questions are now decided (see §2); (e) a second failure-consumption pattern is documented (§8): pause-and-resume, alongside Brickfeed's failover.

Everything in v1 not contradicted here still holds: the service is a LAN-only, credential-free, named-transform local-inference service on the RTX 5070 box. It is **not** a general LLM gateway, **not** for long-form voiced generation, **not** for safety-relevant classification, **not** internet-facing.

---

## 1. Purpose and consumers

A small self-hosted HTTP service exposing named **text → transform → JSON** operations backed by local LLM inference with **constrained decoding** (format drift is structurally impossible).

| Consumer | Transforms used | Failure posture |
|---|---|---|
| Brickfeed News | `image-prompt` | Caller-side failover to Haiku on any 4xx/5xx |
| Scriptorium bakery | `cast-mentions`, `cast-canonicalize`, `scene-update`, `illustration-prompt` | Pause bake, retry later; no paid fallback exists |

The service behaves identically for both: fail fast, fail loudly, machine-distinguishable errors. What callers do with errors is their business.

## 2. Decisions (v1 open questions, now closed)

These are pre-decided so the executor never has to choose. Each becomes an ADR file in cycle T1 (copy the rationale below into the ADR).

**ADR-0001 — Stack: Python 3.12 + FastAPI + uv.** House pattern (Concord, radio-server). Pydantic v2 for request/response models. `httpx` async client for Ollama. `jinja2` for prompt templates. `ruff` + `pytest`. No Docker for v1 — bare uv venv + systemd unit (§9).

**ADR-0002 — Runtime: Ollama.** Chosen over raw llama.cpp server. Rationale: Ollama provides model pull/version management, an HTTP API, **JSON-schema structured outputs** (it compiles the schema to a llama.cpp grammar internally, so we get grammar-constrained decoding without writing GBNF), and per-request `keep_alive` for unload control. Raw llama.cpp buys marginally tighter grammar control at a large assembly cost. **Escape hatch:** the Ollama client lives behind an internal `LLMClient` protocol (§6); if schema fidelity ever proves insufficient, a llama.cpp-server client can be swapped in without touching transforms.

**ADR-0003 — Auth: optional shared-secret header, default off.** If env `TRANSFORM_API_KEY` is set, every `/v1/*` request must carry `X-Transform-Key: <value>` or receive 401. If unset, no auth (LAN posture). `/health` is always unauthenticated.

**ADR-0004 — Style-wrapping is caller-side (v1 §6 recommendation confirmed).** All transforms return *neutral subject* content. Visual style (medium, palette, artist tags, Brickfeed's toy-brick treatment, Scriptorium's per-book styles) is applied by callers. Exception that is *not* style: `illustration-prompt` weaves provided **character visual descriptions** into its output, because character identity is subject, not style.

**ADR-0005 — Concurrency: single in-flight generation.** 12GB card, one model. An asyncio semaphore of 1 serializes all generation. Requests queue up to `QUEUE_WAIT_S` (default 90s); on timeout the service returns 503 with `reason: "busy"`. No multi-model juggling in v1.

**Models (defaults; re-verify exact Ollama tags at build time — the library moves):**

| Role | Model | Why |
|---|---|---|
| Default per-transform binding | `qwen3:8b` (Q4_K_M) | ~5GB, strong instruction-following, leaves VRAM headroom |
| Upgrade path if quality demands | `qwen3:14b` (Q4_K_M) | ~9GB; bench decides per transform |
| Test model (CI on the 5070) | `qwen3:0.6b` | Fast, loads in seconds; used only to prove plumbing, never quality |

**Qwen3 note (important):** Qwen3 is a hybrid thinking model. For these transforms thinking is pure latency — **disable it**. Ollama exposes a `think: false` request field for Qwen3 (verify the current field name in Ollama docs at build time; older workaround is a `/no_think` tag in the prompt). Set non-thinking sampling per Qwen guidance, then override per transform: our defaults are temperature 0.3, top_p 0.8 for extraction-class transforms.

## 3. Core abstraction: the Transform

A transform is a registered bundle. v2 definition:

| Component | Role |
|---|---|
| `name` | Registry key, kebab-case, appears in the URL |
| `template` | Jinja2 prompt template. Receives `text` plus the validated `options` object |
| `model` | Ollama tag |
| `params` | temperature, top_p, max output tokens (`num_predict`), think flag |
| `input_budget` | Max input size in **estimated tokens** (see §5 token estimation) |
| `over_budget` | `"truncate"` (with a named strategy) or `"reject"` (→ 413) |
| `truncation_strategy` | v1 strategies: `lede_first_n` (keep first paragraphs up to budget — news), `head` (keep leading text to budget on a paragraph boundary — book pages) |
| `options_schema` | JSON Schema for the request `options` object. `{}` if the transform takes none. Violation → 400 |
| `output_schema` | JSON Schema enforced two ways: passed to Ollama as the `format` field (constrained decoding) **and** re-validated post-generation |
| `validators` | Ordered post-generation checks beyond the schema: length bounds, banned substrings, cross-field rules. Each returns ok or a reason string |
| `retry_policy` | On validation failure: `retries` (default 1) and `temp_bump` (default +0.15). Exhausted → 422 with the last failure reasons |

**Request pipeline (unchanged shape from v1, now precise):**

```
resolve transform (404 if unknown)
→ auth check (401)
→ validate request shape + options against options_schema (400)
→ estimate input tokens; enforce budget (413 or truncate per strategy)
→ render Jinja2 template(text, options) → messages
→ acquire generation semaphore (503 "busy" on queue timeout)
→ Ollama /api/chat with format=output_schema, stream=false
→ parse JSON, validate against output_schema, run validators
→ on failure: retry per policy with temp bump
→ respond 200 with output + meta, or 422/503 with structured error
```

## 4. API specification

All bodies are JSON. All timestamps ISO-8601 UTC.

### POST `/v1/transform/{name}`

Request:
```json
{
  "text": "…input text…",
  "options": { }
}
```
`options` is transform-specific and validated against the transform's `options_schema`. Omitted `options` ≡ `{}`.

Success — 200:
```json
{
  "output": { "prompt": "…" },
  "meta": {
    "transform": "image-prompt",
    "transform_version": "0.1.0",
    "model": "qwen3:8b",
    "input_tokens_est": 812,
    "truncated": false,
    "attempts": 1,
    "latency_ms": 1043,
    "queued_ms": 12
  }
}
```
`output` always conforms to the transform's `output_schema` — an object, never a bare string.

Errors (body always `{"error": {"code": "...", "message": "...", "detail": {...}}}`):

| HTTP | `error.code` | Meaning | Caller action (typical) |
|---|---|---|---|
| 400 | `bad_request` / `bad_options` | Malformed body, or `options` fails `options_schema` | Fix the call — programmer error |
| 401 | `unauthorized` | Auth enabled, key missing/wrong | Fix config |
| 404 | `unknown_transform` | Name not in registry | Fix the call |
| 413 | `over_budget` | Input over budget and transform policy is `reject` | Shrink input or fall back |
| 422 | `validation_failed` | Generation failed validators after retries; `detail.reasons` lists each attempt's failure | Brickfeed: failover. Scriptorium: retry-with-backoff N times, then park |
| 503 | `busy` / `model_unavailable` | Queue timeout, Ollama unreachable, or model failed to load | Brickfeed: failover. Scriptorium: pause bake, resume later |
| 500 | `internal` | Bug | Report |

### GET `/v1/transforms`

Registry listing:
```json
{ "transforms": [
  { "name": "image-prompt", "version": "0.1.0", "model": "qwen3:8b",
    "input_budget": 3000, "over_budget": "truncate",
    "options_schema": { }, "output_schema": { } }
] }
```

### GET `/health` (unauthenticated)

```json
{ "status": "ok" | "degraded",
  "ready": true,
  "ollama_reachable": true,
  "models_loaded": ["qwen3:8b"],
  "uptime_s": 8641 }
```
`status` is `ok` iff Ollama answers `/api/ps` (liveness). Loaded models come from `/api/ps`. Never 500s — degradation is data, not an error. The additive `ready` field (T14, ADR-0008) is *readiness*: true iff Ollama is reachable **and** the primary model (`TTS_PRIMARY_MODEL`) is resident. `status` semantics are unchanged.

### GET `/ready` (unauthenticated)

```json
{ "ready": true,
  "ollama_reachable": true,
  "models_loaded": ["qwen3:8b"],
  "primary_model": "qwen3:8b",
  "uptime_s": 8641 }
```
Readiness alone (T14, ADR-0008): `ready` iff Ollama is reachable **and** `primary_model` is loaded. Lets a caller distinguish "up but no model resident" (e.g. just after `/v1/models/unload`) from "ready to serve". Never 500s.

### POST `/v1/models/unload`

Body `{"model": "qwen3:8b"}` or `{}` (all loaded). Implementation: for each target, issue a minimal `/api/generate` with `keep_alive: 0` (this is Ollama's supported unload mechanism), then confirm via `/api/ps`. Returns `{"unloaded": ["qwen3:8b"]}`. **This is the endpoint the Scriptorium orchestrator calls before starting a render phase** (GPU phase exclusivity invariant).

## 5. Runtime integration details

**Ollama endpoints used:** `POST /api/chat` (generation; `format` = the output JSON Schema object; `stream: false`; `options: {temperature, top_p, num_predict}`; `keep_alive` from config, overridable per request via a `keep_alive` field in transform request options? — **no**: keep_alive is service config only, `OLLAMA_KEEP_ALIVE` default `"5m"`; callers wanting an unload use `/v1/models/unload`), `GET /api/ps` (health/loaded), `GET /api/tags` (startup check that bound models are pulled — log a loud warning listing any missing).

**Token estimation:** Ollama exposes no tokenizer endpoint, and budgets here are *quality* boundaries, not context limits (Qwen3 context is 32k+; our largest budget is 4k). Estimate: `tokens ≈ ceil(words × 1.35)` where words = whitespace-split count. Document this constant in code; do not add a tokenizer dependency.

**Truncation strategies** operate on paragraphs (split on blank lines):
- `lede_first_n`: keep paragraph 0, then subsequent paragraphs in order while estimate ≤ budget. (News: inverted pyramid.)
- `head`: identical mechanics, named separately so book-page transforms can later diverge (e.g., keep last paragraph too) without renaming.
Both set `meta.truncated = true`.

**Timeouts:** httpx total timeout 120s per generation attempt (first-token on a cold model includes load time; qwen3:14b cold load can take tens of seconds). Cold-load latency is visible in `latency_ms`; that's fine.

## 6. Registry pattern (code shape)

Transforms are Python modules, not config files — type-checked, testable, greppable.

```
src/tts/
  app.py               # FastAPI app, routes, auth dependency
  pipeline.py          # the request pipeline of §3
  llm.py               # LLMClient protocol + OllamaClient + FakeLLMClient
  budget.py            # token estimate + truncation strategies
  registry.py          # Transform dataclass + register() + auto-discovery
  transforms/
    __init__.py        # imports all transform modules (explicit list)
    image_prompt.py
    cast_mentions.py
    cast_canonicalize.py
    scene_update.py
    illustration_prompt.py
```

```python
# registry.py (shape, not final code)
@dataclass(frozen=True)
class Transform:
    name: str
    version: str
    template: str                      # Jinja2 source
    model: str
    temperature: float = 0.3
    top_p: float = 0.8
    num_predict: int = 512
    think: bool = False
    input_budget: int = 3000
    over_budget: Literal["truncate", "reject"] = "truncate"
    truncation_strategy: str = "head"
    options_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    validators: tuple[Validator, ...] = ()
    retries: int = 1
    temp_bump: float = 0.15

REGISTRY: dict[str, Transform] = {}
def register(t: Transform) -> Transform: ...
```

`LLMClient` protocol: `async def chat(messages, format_schema, params) -> str` (returns raw model text; pipeline parses JSON). `FakeLLMClient` is constructed with canned responses (or a callable) and records calls — all non-GPU tests use it.

**Validator library** (in `pipeline.py` or `validators.py`), reused across transforms:
- `max_chars(field, n)` / `min_chars(field, n)`
- `banned_substrings(field, ["**", "##", "http", "```"])` — kills markdown/URL leakage
- `no_empty_strings(field)` — for string arrays
- `word_range(field, lo, hi)`
- transform-specific lambdas allowed inline.

---

## 7. Transform catalog

Five transforms ship in v1. For each: contract, full `output_schema`, `options_schema`, budget, binding, validators, and the **v0 prompt template verbatim** (Jinja2; `{{ text }}` is the post-truncation input). Templates are starting points — refinement during T4–T6 is expected, but only against fixture evidence, and every template change bumps `transform_version`.

Common system framing prepended to every transform's system message:

```
You are a precise text-processing function. You return only JSON matching the
required schema. You never add commentary, markdown, or fields not in the schema.
When evidence is absent, follow the transform's rules for defaults; never invent
specific facts not supported by the input.
```

### 7.1 `image-prompt` (v0) — Brickfeed's news-story → image subject prompt

Unchanged in intent from v1 §6. Input: a news story. Output: one neutral subject prompt.

- Budget: 3000 est-tokens, `truncate` / `lede_first_n`
- Model: `qwen3:8b`, temp 0.4, num_predict 160
- `options_schema`: `{}` 
- `output_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["prompt"],
  "properties": { "prompt": { "type": "string", "minLength": 30, "maxLength": 400 } } }
```
- Validators: `banned_substrings(prompt, ["**","##","http","\n"])`, `word_range(prompt, 8, 60)`
- Template (system+user):
```
SYSTEM: {common framing}
You convert a news story into one concise image-generation subject prompt.

USER:
Story:
"""
{{ text }}
"""
Write one subject prompt for a single illustrative image of this story.
Rules:
- Describe one concrete visual scene: subject, action, setting.
- 15–50 words, one line, no lists.
- No style, medium, camera, or artist words (added downstream).
- No text/typography in the scene. No logos.
- Prefer the story's primary subject; if multiple topics, pick the most visual.
Return JSON: {"prompt": "..."}
```

### 7.2 `cast-mentions` — per-page character mention extraction (Scriptorium P1)

Called once per logical page, any order, parallel-safe. Extracts who is mentioned and **verbatim** physical descriptors. The caller reduces mentions across pages (grouping by name/alias is caller-side, deterministic — see scriptorium-DESIGN §7 P2).

- Budget: 1600 est-tokens (pages are ≤850 words), `reject` (a page over budget is a paginator bug — fail loudly)
- Model: `qwen3:8b`, temp 0.2, num_predict 700
- `options_schema`: `{}` 
- `output_schema`:
```json
{ "type": "object", "additionalProperties": false, "required": ["mentions"],
  "properties": { "mentions": { "type": "array", "maxItems": 15, "items": {
    "type": "object", "additionalProperties": false,
    "required": ["name", "aliases", "descriptors", "is_person"],
    "properties": {
      "name":        { "type": "string", "minLength": 1, "maxLength": 60 },
      "aliases":     { "type": "array", "items": {"type": "string", "maxLength": 60}, "maxItems": 6 },
      "descriptors": { "type": "array", "items": {"type": "string", "maxLength": 140}, "maxItems": 8 },
      "is_person":   { "type": "boolean" }
    } } } } }
```
- Validators: `no_empty_strings(mentions[].name)`; drop-nothing rule — validators never mutate output.
- Template:
```
SYSTEM: {common framing}
You extract character mentions from one page of a book.

USER:
Page text:
"""
{{ text }}
"""
List each distinct character (person, or named non-human agent like a ship or
creature acting as a character) mentioned on this page.
Rules:
- "name": the most specific name/label used on THIS page ("the Time Traveller",
  "Weena", "the innkeeper"). Keep the article if the text uses one.
- "aliases": other labels used for the same character on this page.
- "descriptors": verbatim phrases from the text describing physical appearance,
  clothing, age, or bearing. Quote the text's words; do not paraphrase or invent.
  Empty array if none.
- "is_person": false for animals, ships, machines, crowds.
- Skip characters only referenced abstractly ("his late father") unless described.
Return JSON: {"mentions": [...]}
```

### 7.3 `cast-canonicalize` — evidence → paintable canonical description (Scriptorium P2)

Called once per major character. Input `text` is empty-ish (`""` allowed); the evidence rides in `options`.

- Budget: 1200, `truncate`/`head` (applies to `text`, which is normally empty)
- Model: `qwen3:8b`, temp 0.5 (mild creativity for gap-filling defaults), num_predict 400
- `options_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["name", "descriptors"],
  "properties": {
    "name":        { "type": "string" },
    "aliases":     { "type": "array", "items": {"type": "string"} },
    "descriptors": { "type": "array", "items": {"type": "string"}, "maxItems": 40 },
    "era":         { "type": "string" },
    "genre":       { "type": "string" } } }
```
- `output_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["visual_description", "one_line", "tags"],
  "properties": {
    "visual_description": { "type": "string", "minLength": 80, "maxLength": 700 },
    "one_line":           { "type": "string", "minLength": 15, "maxLength": 160 },
    "tags":               { "type": "array", "items": {"type": "string", "maxLength": 30}, "maxItems": 8 } } }
```
- Validators: `banned_substrings(visual_description, ["**","\n\n","personality","brave","kind"])` — the last two are cheap guards against trait-drift into non-visual territory; refine during T5.
- Template:
```
SYSTEM: {common framing}
You write canonical VISUAL descriptions of book characters for an illustrator.

USER:
Character: {{ options.name }}{% if options.aliases %} (also called: {{ options.aliases | join(", ") }}){% endif %}
Era/setting: {{ options.era | default("unspecified") }}. Genre: {{ options.genre | default("unspecified") }}.
Evidence — verbatim descriptors collected from the text:
{% for d in options.descriptors %}- "{{ d }}"
{% endfor %}
Write:
- "visual_description": 2–4 sentences a painter could work from — apparent age,
  build, hair, face, characteristic clothing. Use ONLY the evidence; where the
  evidence is silent, choose ONE plain era-appropriate default rather than
  something distinctive. No personality, no plot, no names of other characters.
- "one_line": the same person in ≤20 words (used inside image prompts).
- "tags": 3–8 short visual tags ("grey beard", "red cloak").
Return JSON.
```

### 7.4 `scene-update` — page → updated scene ledger + salience score (Scriptorium P3)

Called once per page **strictly in order**; the caller threads the returned ledger into the next call. This single pass produces both continuity state and the selection scores.

- Budget: 1600, `reject` (same paginator-bug logic as 7.2)
- Model: `qwen3:8b`, temp 0.2, num_predict 500
- `options_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["prior_ledger", "cast_names"],
  "properties": {
    "prior_ledger": { "type": ["object", "null"] },
    "cast_names":   { "type": "array", "items": {"type": "string"}, "maxItems": 40 },
    "era":          { "type": "string" } } }
```
  (`prior_ledger: null` on page 1. `cast_names` = canonical names from cast.json, so the model normalizes "the Traveller" → the canonical label when confident.)
- `output_schema` (this object IS the ledger, stored verbatim on the page):
```json
{ "type": "object", "additionalProperties": false,
  "required": ["location", "time_of_day", "atmosphere", "present",
               "scene_changed", "visual_salience", "best_visual_beat", "carry_notes"],
  "properties": {
    "location":        { "type": "string", "maxLength": 120 },
    "time_of_day":     { "enum": ["dawn","morning","midday","afternoon","evening","night","unknown"] },
    "atmosphere":      { "type": "string", "maxLength": 120 },
    "present":         { "type": "array", "items": {"type": "string", "maxLength": 60}, "maxItems": 12 },
    "scene_changed":   { "type": "boolean" },
    "visual_salience": { "type": "number", "minimum": 0, "maximum": 1 },
    "best_visual_beat":{ "type": "string", "minLength": 15, "maxLength": 220 },
    "carry_notes":     { "type": "string", "maxLength": 200 } } }
```
- Validators: `banned_substrings(best_visual_beat, ["\n"])`.
- Template:
```
SYSTEM: {common framing}
You maintain a rolling scene ledger while reading a book page by page, and you
score each page's illustration potential.

USER:
{% if options.prior_ledger %}Ledger after the previous page:
{{ options.prior_ledger | tojson }}
{% else %}This is the first page; there is no prior ledger.
{% endif %}
Known cast (use these exact names in "present" when they match): {{ options.cast_names | join(", ") }}
{% if options.era %}Era/setting: {{ options.era }}.{% endif %}

Page text:
"""
{{ text }}
"""
Update the ledger for the END of this page:
- Carry location/time forward unchanged unless the text moves them.
- "scene_changed": true only if the narrative moved to a new location or made a
  clear time jump ON this page.
- "present": characters physically present at page end (canonical names when known).
- "visual_salience": 0–1. High (≥0.7): vivid action, striking imagery, a reveal,
  strong atmosphere. Low (≤0.3): abstract discussion, summary, transitional prose.
- "best_visual_beat": ONE present-tense sentence describing the single most
  illustratable moment on this page, concrete and specific.
- "carry_notes": ≤200 chars of continuity facts a future illustrator needs
  (injuries, held objects, weather) — cumulative but pruned to what still matters.
Return JSON matching the ledger schema exactly.
```

### 7.5 `illustration-prompt` — (page, ledger, cast) → SDXL subject prompt (Scriptorium P5)

Called once per **selected** page, any order.

- Budget: 1600, `reject`
- Model: `qwen3:8b` default; if the M1 blind read shows subject-selection weakness, this is the first transform to try on `qwen3:14b`
- temp 0.6, num_predict 350
- `options_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["ledger", "cast"],
  "properties": {
    "ledger": { "type": "object" },
    "cast":   { "type": "array", "maxItems": 6, "items": {
      "type": "object", "required": ["name", "one_line"],
      "properties": { "name": {"type":"string"}, "one_line": {"type":"string"} } } },
    "era":    { "type": "string" } } }
```
  (Caller passes cast entries only for characters in `ledger.present`, capped at the 4 most mention-frequent.)
- `output_schema`:
```json
{ "type": "object", "additionalProperties": false,
  "required": ["prompt", "depicted", "shot"],
  "properties": {
    "prompt":   { "type": "string", "minLength": 60, "maxLength": 600 },
    "depicted": { "type": "array", "items": {"type": "string"}, "maxItems": 4 },
    "shot":     { "enum": ["wide", "medium", "close"] },
    "avoid":    { "type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 6 } } }
```
- Validators: `word_range(prompt, 20, 90)`, `banned_substrings(prompt, ["**","\n","style of","photograph","oil painting","watercolor","engraving"])` — the medium words are caller-side; their appearance here is drift.
- Template:
```
SYSTEM: {common framing}
You write image-generation subject prompts for book illustrations.

USER:
Scene ledger for this page:
{{ options.ledger | tojson }}
{% if options.era %}Era/setting: {{ options.era }}.{% endif %}
Characters available (weave their descriptions in IF depicted; never use a bare
name without its description):
{% for c in options.cast %}- {{ c.name }}: {{ c.one_line }}
{% endfor %}
Page text:
"""
{{ text }}
"""
Write ONE subject prompt depicting this page's best visual beat
("{{ options.ledger.best_visual_beat }}") — you may choose a better beat from the
page text if one exists.
Rules:
- One moment, one composition. 30–80 words, one line.
- Ground the scene: setting, time of day, atmosphere from the ledger.
- For each depicted character, include their visual identifiers from the list
  above (condensed), not just their name.
- No style/medium/artist words. No text or lettering in the scene.
- "shot": wide (environment-dominant), medium (figures in setting), close (faces/objects).
- "avoid": up to 6 short negative hints specific to this scene (e.g., "modern
  clothing", "crowds") — omit generic quality terms.
Return JSON.
```

**Worked micro-example (scene-update → illustration-prompt), for test fixtures:**
Input page: the Time Traveller demonstrating the model machine in his lamplit smoking-room. Expected-shape ledger: `location: "the Time Traveller's smoking-room, Richmond"`, `time_of_day: "evening"`, `atmosphere: "lamplit, expectant"`, `present: ["the Time Traveller","Filby","the Psychologist","the Medical Man"]`, `scene_changed: false`, `visual_salience: ~0.8`, `best_visual_beat: "The tiny model machine blurs, becomes indistinct, and vanishes from the table."` Then illustration-prompt output-shape: `prompt: "A small intricate brass-and-ivory machine shimmering into transparency on a parlor table, four Victorian gentlemen leaning in around it, one — a pale grey-eyed man with a shock of white-streaked hair — with his hand still outstretched, lamplight and pipe smoke in a cluttered 1890s smoking-room"`, `depicted: ["the Time Traveller"]`, `shot: "medium"`. Fixtures assert **shape and grounding**, not exact wording.

### 7.6 Deferred registry entries (documented, not built)

`chapter-mood` (chapter → mood tags for Navidrome playlists) and `summarize-blurb` (book → back-cover blurb for the shelf UI). Listed so names are reserved; no cycles allocated.

## 8. Failure & consumption patterns

The service's contract is identical for all callers: **fast, loud, machine-distinguishable**. Two documented consumption patterns:

- **Failover (Brickfeed):** any 4xx/5xx → immediately use the Haiku provider. Service downtime costs pennies, never a failed cycle.
- **Pause (Scriptorium):** no paid fallback exists. 503-class → the bake job transitions to `waiting_gpu` and is retried on a schedule (orchestrator's tick). 422 on a unit → retry that unit up to 3 times with backoff (the service already retried internally once); still failing → mark the unit `failed`, continue the phase, surface in the review UI. 400/404/413 → bug; halt the phase loudly.

Nothing in this section changes service code — it constrains the error taxonomy's stability. **Error codes are API; changing them is a breaking change.**

## 9. Ops

**Config (env, all optional):**

| Var | Default | Meaning |
|---|---|---|
| `TTS_PORT` | `8712` | Bind port |
| `TTS_HOST` | `0.0.0.0` | LAN bind |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Runtime |
| `OLLAMA_KEEP_ALIVE` | `5m` | Passed on every generate |
| `TRANSFORM_API_KEY` | unset | Enables auth when set |
| `QUEUE_WAIT_S` | `90` | Semaphore queue timeout (max wait for the slot) |
| `MAX_QUEUE_DEPTH` | `0` | Max requests waiting for the slot; `0` = unbounded. Overflow fast-fails `503 busy` (T14, ADR-0008) |
| `TTS_PRIMARY_MODEL` | `qwen3.5:9b` | Model whose residency defines readiness (`/ready`, `/health.ready`; T14, ADR-0008) |
| `TTS_LOG_LEVEL` | `INFO` | |

**Logging:** one structured line per request (JSON): `ts, request_id, transform, status, attempts, input_tokens_est, truncated, queued_ms, latency_ms, error_code?`. Request id also returned in an `X-Request-Id` response header.

**systemd** (installed manually in T7, unit file committed to `deploy/`):
```ini
[Unit]
Description=text-transform-service
After=network-online.target ollama.service
Wants=ollama.service
[Service]
User=kris
WorkingDirectory=/opt/text-transform-service
ExecStart=/opt/text-transform-service/.venv/bin/uvicorn tts.app:app --host 0.0.0.0 --port 8712
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

**GPU coexistence:** this service never introspects the GPU and never coordinates with imagegen-service. Coordination is the caller's job (Scriptorium orchestrator calls `/v1/models/unload` before rendering). `OLLAMA_KEEP_ALIVE=5m` means idle periods self-unload anyway.

## 10. Testing strategy

- **Unit (no GPU, run anywhere):** budget estimation, both truncation strategies (golden inputs), options/output schema validation, validator library, retry policy (FakeLLM returns bad-then-good), error taxonomy (each code has a test), auth on/off.
- **Contract fixtures:** for each transform, ≥3 fixture inputs with *shape assertions* on FakeLLM-simulated outputs (fixtures double as documentation and as Scriptorium's recorded fixtures — see scriptorium-BUILD-PLAN S5).
- **GPU integration (`pytest -m gpu`, runs only on the 5070):** each transform once against `qwen3:0.6b` asserting only *schema conformance and pipeline mechanics* (0.6b output quality is irrelevant); one smoke per real bound model asserting non-empty schema-valid output.
- **Never** assert exact LLM wording anywhere.

## 11. Brickfeed bench (deferred track, unchanged from v1 §9)

The 30-story dual-provider image-set comparison gates Brickfeed's provider cycle, not Scriptorium. Scriptorium's quality gate is the M1 first-bake review. Bench harness = cycle T8, unscheduled.
