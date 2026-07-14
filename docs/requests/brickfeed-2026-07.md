<!--
Provenance: copied verbatim from the brickfeed-news repo at `docs/tts-transform-requests.md`,
commit brickfeed@40acb90, imported into text-transform-service for Cycle T9 (2026-07-13).

This is the CONSUMER's request, not the TTS contract. Where a request conflicts with the TTS
charter or house conventions, the *reconciled* contract that actually ships lives in the
transform module docstring plus the CYCLE-LOG entry for that cycle — read those as binding, not
this doc. As of T9: `story-cover` is built (reconciled); `opinion-gate` is HELD as out of the
DESIGN §1 charter ("not for safety-relevant classification") pending a product-owner call;
`opinion-piece` + `opinion-image-brief` are T10.
-->

# TTS transform requests (Brickfeed gaps)

Brickfeed News wants a local-first option (via `text-transform-service`, TTS) for the four
generation tasks it currently routes through Claude/Haiku. **None of the four has a matching
transform in the live TTS registry** (see `docs/tts-inventory.md` and ADR-0021), so each is a
GAP requesting a new transform.

These transforms are built in the **TTS repo**, per its "adding a transform" recipe — not here.
Until a given transform is registered, that task **stays on the incumbent Claude provider**.

**Do NOT reuse the existing `image-prompt` transform for any of these.** Its output is
`{prompt}` only; every Brickfeed task below needs a different, richer schema (5-field story
bundle, per-id verdict array, title+body, or imagePrompt+caption). Bodging Brickfeed prompts
through a mismatched schema is explicitly out of scope.

Each request below states the proposed transform name, the input text + options schema, the
output JSON schema, a budget/truncation suggestion, and two example input/output pairs.

All four share these hard rules (already enforced in the incumbent prompts, must be preserved):

- Any `imagePrompt`: ~15–30 words, playful/cartoonish, purely visual; **no** text/letters/
  numbers/signs/logos/speech-bubbles/written-words; **no** brand names or trademarks; describe a
  real photographed scene, **not** pre-stylized as a miniature/figurine/sculpture/block-build
  (brick styling is applied downstream by Brickfeed, not the model).
- Any `caption`: ~8–15 words, describes the same scene, same no-text/no-brand rules, **no**
  appended credit/byline/attribution.
- `headline`/`description`: ORIGINAL rewrites, never verbatim source text.

---

## 1. `story-cover` — GAP for Task 1 (story cover bundle)

Incumbent: `buildGenerationPrompt` (`src/prompt.ts:62`) → `parseGeneratorOutput`
(`src/generator/parse.ts`). Mirrors `GeneratorOutput` (`src/types.ts:85`).

**Input text.** The source story context:
```
Source article title: <title>
Publisher: <sourceName or "unknown source">
Source URL: <url>
```

**Options schema.** `{}` (no options).

**Output JSON schema.**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["headline", "description", "imagePrompt", "category", "caption"],
  "properties": {
    "headline":    { "type": "string", "minLength": 10, "maxLength": 200 },
    "description": { "type": "string", "minLength": 40, "maxLength": 600 },
    "imagePrompt": { "type": "string", "minLength": 30, "maxLength": 400 },
    "category":    { "enum": ["WORLD","POLITICS","BUSINESS","TECHNOLOGY","SCIENCE","SPORTS","CULTURE","OPINION"] },
    "caption":     { "type": "string", "minLength": 15, "maxLength": 160 }
  }
}
```
`category` must be exactly one of the eight values (source of truth: `CATEGORIES`,
`src/category.ts`). Note: Brickfeed normalizes an out-of-set category to `WORLD` rather than
failing, but the transform should still emit a valid enum value.

**Budget/truncation.** `input_budget` ~1200; `over_budget: truncate` (story context is short;
truncating the tail of a long title is harmless).

**Example A**
- Input:
  ```
  Source article title: City council approves new bike lane network downtown
  Publisher: Metro Herald
  Source URL: https://example.com/bike-lanes
  ```
- Output:
  ```json
  {
    "headline": "Downtown gains a connected grid of protected bike lanes",
    "description": "The city council voted to build a network of protected bike lanes across the downtown core, aiming to link existing routes, calm traffic, and give cyclists a continuous path. Construction is expected to begin next year.",
    "imagePrompt": "A jubilant crowd of cyclists in bright helmets pedals down a sunlit street lined with fresh green-painted lanes and tiny potted trees",
    "category": "BUSINESS",
    "caption": "Cheerful cyclists stream down a freshly painted downtown avenue at midday"
  }
  ```

**Example B**
- Input:
  ```
  Source article title: Astronomers spot unusually fast-spinning distant star
  Publisher: unknown source
  Source URL: https://example.com/star
  ```
- Output:
  ```json
  {
    "headline": "Distant star clocked spinning at a startling pace",
    "description": "Astronomers observed a far-off star rotating far faster than typical stars of its class, a finding that could sharpen models of how such objects form and shed angular momentum. Follow-up observations are planned.",
    "imagePrompt": "A wide-eyed cartoon star whirls like a top in deep space, flinging sparkles outward while tiny planets duck and spin around it",
    "category": "SCIENCE",
    "caption": "A dizzy spinning star flings sparkles across a starry sky"
  }
  ```

---

## 2. `opinion-gate` — GAP for Task 2 (topic-gate classifier)

Incumbent: `buildGatePrompt` (`src/opinions.ts:187`) → `parseGateVerdicts` (`src/opinions.ts:213`).
One batched call; **fail-closed** (any malformed response = all stories excluded).

**Input text.** A JSON array of candidate stories:
```json
[{"id": "<story id>", "title": "<our headline>", "summary": "<our description>"}]
```

**Options schema.** `{}`.

**Output JSON schema.**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["verdicts"],
  "properties": {
    "verdicts": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "verdict", "reason"],
        "properties": {
          "id":      { "type": "string" },
          "verdict": { "enum": ["eligible", "excluded"] },
          "reason":  { "type": "string", "maxLength": 200 }
        }
      }
    }
  }
}
```
Exactly one verdict per input id, each id echoed exactly once. Intent: classify whether each
story is acceptable source material for a lighthearted satirical opinion section; **exclude**
anything centering tragedy, violence, death, disaster casualties, or victims; **if uncertain,
exclude.**

**Budget/truncation.** `input_budget` ~1600; `over_budget: reject` — silently truncating the
candidate list would drop stories from classification (they'd be treated as excluded), so a
reject is safer than a partial batch.

**Example A**
- Input: `[{"id":"a1","title":"Town's giant pumpkin smashes state record","summary":"A local grower's 2,300-pound pumpkin took the state fair title."},{"id":"b2","title":"Fatal multi-car crash closes interstate","summary":"Several people died in a pileup during morning fog."}]`
- Output:
  ```json
  {"verdicts":[
    {"id":"a1","verdict":"eligible","reason":"Lighthearted fair story, no harm."},
    {"id":"b2","verdict":"excluded","reason":"Centers deaths in a crash."}
  ]}
  ```

**Example B**
- Input: `[{"id":"c3","title":"Mayor caught napping during ribbon-cutting","summary":"A viral clip shows the mayor dozing off at a ceremony."},{"id":"d4","title":"Charity gala raises funds for storm victims","summary":"An event raised money for families displaced by flooding."}]`
- Output:
  ```json
  {"verdicts":[
    {"id":"c3","verdict":"eligible","reason":"Harmless viral moment."},
    {"id":"d4","verdict":"excluded","reason":"Centers disaster victims."}
  ]}
  ```

---

## 3. `opinion-piece` — GAP for Task 3 (opinion piece)

Incumbent: `buildOpinionPrompt` (`src/opinions.ts:283`) → `splitTitleBody` (`src/opinions.ts:324`).
The incumbent returns plain text (first line = title, blank line, then body). For a TTS
transform, prefer a **structured** output so the split is unambiguous.

**Input text.** The persona voice/body prose + shared instructions, plus either the selected
news article blocks (headline/description/`(via sourceName: title)`) or the letters-column rules.

**Options schema.**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["minWords", "maxWords"],
  "properties": {
    "minWords": { "type": "integer", "minimum": 50, "maximum": 2000 },
    "maxWords": { "type": "integer", "minimum": 50, "maximum": 2000 }
  }
}
```
(word range from `lengthRangeFor(persona)`, `src/opinions.ts:274`.)

**Output JSON schema.**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["title", "body"],
  "properties": {
    "title": { "type": "string", "minLength": 3, "maxLength": 160 },
    "body":  { "type": "string", "minLength": 200 }
  }
}
```
Intent: write one satirical opinion piece in the persona's voice reacting to ONE supplied
article (or, for letters personas, invent + answer a reader letter). `body` length within
`[minWords, maxWords]`.

**Budget/truncation.** `input_budget` ~3000; `over_budget: truncate` (persona prose + a handful
of article blocks; trimming trailing articles degrades gracefully).

**Example A** (news persona, range 200–350)
- Input (abridged): `<persona voice: a pompous local-affairs columnist>` + `ARTICLE 1: Downtown gains protected bike lanes / The council approved a connected grid…`
- Output:
  ```json
  {
    "title": "The Tyranny of the Two-Wheeled Elite",
    "body": "It has come to my attention that our council, in its infinite wisdom, has bestowed upon the cycling classes a ribbon of green paint stretching the length of downtown… (≈300 words in the columnist's overwrought voice)"
  }
  ```

**Example B** (letters persona)
- Input (abridged): `<persona voice: an unflappable advice columnist>` + letters rules.
- Output:
  ```json
  {
    "title": "Dear Cluttered in Cleveland",
    "body": "Dear Aunt Prudence, my neighbor keeps borrowing my ladder and returning it one rung shorter… My dear Cluttered, a ladder, like a reputation, is measured by what remains… (≈250 words, invents then answers the letter)"
  }
  ```

---

## 4. `opinion-image-brief` — GAP for Task 4 (opinion image brief)

Incumbent: `buildImageBriefPrompt` (`src/opinions.ts:358`) → `parseImageBrief` (`src/opinions.ts:405`).
Both keys required as non-empty strings; any deviation fails the author (nothing stored, retried).

**Input text.** The finished piece (`title` + `body`) plus subject context — for news personas
the source article blocks; for letters personas a phrase describing the invented letter's
situation.

**Options schema.** `{}`.

**Output JSON schema.**
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["imagePrompt", "caption"],
  "properties": {
    "imagePrompt": { "type": "string", "minLength": 30, "maxLength": 400 },
    "caption":     { "type": "string", "minLength": 15, "maxLength": 160 }
  }
}
```
Intent: depict the story/letter **subject** — never the author, never the act of writing or
publishing. Same hard image/caption rules as above.

**Budget/truncation.** `input_budget` ~3000; `over_budget: truncate` (the piece body dominates;
truncating its tail still leaves enough subject signal for a brief).

**Example A**
- Input (abridged): `Title: The Tyranny of the Two-Wheeled Elite` + body + `ARTICLE 1: Downtown gains protected bike lanes…`
- Output:
  ```json
  {
    "imagePrompt": "A gaggle of self-important cyclists in tiny top hats pedals down a green-painted lane while a flustered pedestrian clutches a briefcase on the curb",
    "caption": "Top-hatted cyclists rule a freshly painted lane as a pedestrian frets"
  }
  ```

**Example B**
- Input (abridged): `Title: Dear Cluttered in Cleveland` + body + subject = "a neighbor who keeps shortening a borrowed ladder".
- Output:
  ```json
  {
    "imagePrompt": "A puzzled homeowner holds a comically stubby ladder in a driveway while a sheepish neighbor tiptoes away clutching a single sawed-off rung",
    "caption": "A homeowner eyes an absurdly shortened ladder as a neighbor sneaks off"
  }
  ```
