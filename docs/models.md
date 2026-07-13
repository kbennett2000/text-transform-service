# Models

Records the Ollama models resolved on the build box (DESIGN §0.1). The executor never
silently substitutes models; if the DESIGN §2 tags are absent, a human picks same-weight-class
replacements and updates this file.

## ✅ RESOLVED — model rebind (cycle T3, 2026-07-13)

The DESIGN §2 tags (`qwen3:8b`, `qwen3:0.6b`) are **not** available on the box and no longer
exist as separate tags in the pulled library; the box carries the newer **`qwen3.5:*`**
generation. Per the T3 kickoff, the human decision (already made) is to rebind to the same
weight classes in that newer family — **no pulls required**:

| Role | DESIGN §2 tag (absent) | **Resolved binding (T3)** | Weight class | Present? |
|---|---|---|---|---|
| Default per-transform binding | `qwen3:8b` (Q4_K_M) | **`qwen3.5:9b`** | ~6.6 GB | ✅ |
| Test model (CI on the 5070) | `qwen3:0.6b` | **`qwen3.5:2b`** | ~2.7 GB | ✅ |
| Upgrade path (deferred) | `qwen3:14b` (Q4_K_M) | (defer; try `qwen3.5:9b`→larger if a transform demands) | — | — |

**Deviation from DESIGN §2** (recorded here and in CYCLE-LOG per the "never substitute silently"
rule): production transforms T4–T6 bind **`qwen3.5:9b`** where DESIGN §2 wrote `qwen3:8b`; the
dev-only `echo` transform and the GPU test suite use **`qwen3.5:2b`** where DESIGN §2 wrote
`qwen3:0.6b`. Same roles, same weight classes, newer family. The `qwen3.5:9b` default was chosen
over `qwen3.5:4b`/`lfm2.5:8b`/`llama3.1:8b` because it is the closest instruction-following peer
of the intended `qwen3:8b` and leaves VRAM headroom on the 12 GB card (~6.6 GB model).

## `ollama list` (verbatim, 2026-07-13, cycle T3)

```
NAME           ID              SIZE      MODIFIED
qwen3.5:2b     324d162be6ca    2.7 GB    4 weeks ago
qwen3.5:4b     2a654d98e6fb    3.4 GB    4 weeks ago
lfm2.5:8b      9cf756159fc2    5.2 GB    4 weeks ago
llama3.1:8b    46e0c10c039e    4.9 GB    4 weeks ago
qwen3.5:9b     6488c96fa5fa    6.6 GB    4 weeks ago
```

Ollama version: `0.30.7`. `ollama show qwen3.5:2b` capabilities: `completion, vision, tools,
thinking`; context length 262144; quantization Q8_0.

## Qwen3.5 "disable thinking" — VERIFIED (cycle T3)

DESIGN §2 requires thinking be **disabled** (pure latency for these extraction transforms).
Verified empirically against Ollama 0.30.7 with live calls:

- The mechanism is the **top-level `think` boolean** on the request (not inside `options`).
- `think: false` → the response `message`/`response` carries **no `thinking` field**; output is
  clean. `think: true` → a populated `thinking` field appears (confirmed contrast on `qwen3.5:2b`).
- Verified together with constrained decoding (`format` + `think:false`) — both apply
  simultaneously. **Conclusion: thinking is reliably disabled via `think: false`.** The old
  `/no_think` prompt tag is unnecessary on this version.

## ⚠️ IMPORTANT — `format` (constrained decoding) works on `/api/generate`, NOT `/api/chat`

Verified empirically on Ollama 0.30.7 (deterministic, repeated):

- **`POST /api/chat` with `format` = a JSON schema → the schema is IGNORED.** A prompt that does
  not itself ask for JSON ("Write one short sentence about the sea") returns plain prose; even
  `format: "json"` is ignored. No grammar-constrained decoding happens.
- **`POST /api/generate` with the same `format` → the schema IS enforced.** The identical prose
  prompt is forced into schema-valid JSON (`{"sentence": "..."}`); a numeric schema forces a
  number. This is the real ADR-0002 grammar-constrained decoding.

**Consequence / decision (T3, human-approved):** the `OllamaClient` uses **`/api/generate`**, not
the `/api/chat` written in DESIGN §5, so that ADR-0002 / §1 ("format drift is structurally
impossible") actually holds on this box. The pipeline's rendered `[{system}, {user}]` messages map
to `/api/generate`'s `system` and `prompt` fields. Everything else (`think`, `keep_alive`,
`options:{temperature,top_p,num_predict}`, `stream:false`, `format`) is identical. `/health` still
uses `/api/ps` + `/api/tags` (unchanged). If a future Ollama fixes `/api/chat` format enforcement,
switching back is a localized change inside `OllamaClient`. Recorded as a deviation in CYCLE-LOG.
