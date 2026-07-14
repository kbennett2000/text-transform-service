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

## ⚠️ HOST BINDING — `q8_0` KV cache is REQUIRED for large opinion-gate batches (cycle T13)

**`opinion-gate` at batch volume needs the Ollama daemon configured with a quantized KV cache.**
This is a **host-level Ollama setting** (a systemd drop-in on `ollama.service`), not a TTS config
var — TTS cannot set it per request (see the "per-request `flash_attn` is ignored" note below).

```
# /etc/systemd/system/ollama.service.d/flash-attn.conf  (shipped: deploy/ollama.service.d/…)
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```

**The problem it fixes.** `opinion-gate` classifies up to ~100 candidates per call, so TTS sizes
`num_ctx` to the transform budget (`input_budget + num_predict + 1024` = **14144**, cycle T12). At
that context, `qwen3.5:9b` with the default **f16** KV cache under flash attention **closes the
verdict array early** under temperature 0 — a *silent* tail-drop (e.g. 27/34 verdicts,
`truncated=0`, no error), which the caller's fail-closed rule then turns into over-exclusion of the
batch tail. This is strictly worse than a loud failure. Quantizing the KV cache to **q8_0** removes
the instability entirely.

**Empirical evidence (T13, 5070, verified against a throwaway daemon at `num_ctx=14336`, 3
consecutive runs, byte-identical):**

| config | 21-batch | 34-batch | 60-batch | GPU / VRAM | 34-latency |
|---|---|---|---|---|---|
| flash **on**, **f16** KV | 17/21 ✗ | 27/34 ✗ | tail-drop ✗ | 100% GPU | ~15–28 s |
| flash **off**, f16 KV | 21/21 ✓ | complete | complete | **74% CPU** offload | ~500 s ✗ |
| flash **off**, q8_0 KV | — | — | — | **segfault on load** | — |
| flash **on**, **q8_0** KV | **21/21 ✓** | **34/34 ✓** | **60/60 ✓** | **100% GPU, ~4 GB free** | **~22 s ✓** |

The winning row is the binding above. Two hard facts it rests on:

1. **`OLLAMA_FLASH_ATTENTION=1` is mandatory, not optional.** llama.cpp refuses V-cache
   quantization without flash attention: `llama_init_from_model: V cache quantization requires
   flash_attn` → the server segfaults on model load. So "flash-off + q8_0" is physically
   impossible; q8_0 forces flash on. (The box already ran flash `auto`→on, so this only pins it.)
2. **The tail-drop was the f16 KV cache, not flash attention itself** (an earlier T13 hypothesis
   blamed flash). Same flash-on setting, only the KV dtype changes f16→q8_0, and completeness goes
   from 27/34 to 34/34. Flash-**off** is complete but CPU-offloads at 14336 ctx (the non-flash
   attention compute buffer no longer fits the 12 GB card → 74% CPU → ~500 s), so it is not a
   usable option on this hardware.

**Host-wide scope / safety.** This affects every model + transform on the box, not just
opinion-gate. It is safe: the other transforms use small contexts (their KV cache is a few MiB
either way) and q8_0 KV is an imperceptible quality change for these extraction tasks.

### per-request `flash_attn` / KV type is IGNORED by Ollama 0.30.7 (why this is a host binding)

Ollama 0.30.7 does **not** honor `options.flash_attn` (or a per-request KV cache type) on
`/api/generate` — the runner is launched from the **daemon-level** env (`--flash-attn`,
`--cache-type-k/v`) once per model load, and per-request overrides are dropped. That is why the fix
must live on the `ollama.service` unit and cannot be a `Transform` field or an `OllamaClient`
option. If a future Ollama exposes per-request KV/attention control, this could move into TTS.
