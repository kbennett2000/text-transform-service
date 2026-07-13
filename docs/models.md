# Models

Records the Ollama models resolved on the build box (DESIGN §0.1). The executor never
silently substitutes models; if the DESIGN §2 tags are absent, a human picks same-weight-class
replacements and updates this file.

## ⚠️ BLOCKER — required models are NOT present (recorded 2026-07-13, cycle T1)

DESIGN §2 binds these tags:

| Role | Required tag | Present on box? |
|---|---|---|
| Default per-transform binding | `qwen3:8b` (Q4_K_M) | **NO** |
| Test model (CI on the 5070) | `qwen3:0.6b` | **NO** |
| Upgrade path (deferred) | `qwen3:14b` (Q4_K_M) | NO (deferred anyway) |

Neither `qwen3:8b` nor `qwen3:0.6b` is installed. Per the cycle-T1 hard rule, **no
substitute was chosen by the executor.** T1 ships regardless because none of its code binds
a model (no transforms, no generation) — but **cycles T3+ are blocked** until this is
resolved by a human.

**Human action required (one of):**
1. Pull the specified tags: `ollama pull qwen3:8b && ollama pull qwen3:0.6b`, then re-run
   `ollama list` and update this file; **or**
2. If those tags no longer exist in the Ollama library, choose same-weight-class
   replacements (a ~5GB Q4 instruction model as the default binding; a sub-1GB model as the
   fast CI/test model), record them here, and update the transform bindings in DESIGN §2 /
   the transform modules accordingly.

Note: the box currently carries a newer **`qwen3.5:*`** generation (2b / 4b / 9b) rather than
`qwen3:*`. These are plausible replacement candidates in the same weight classes (e.g.
`qwen3.5:9b` as the default binding, `qwen3.5:2b` as the fast model), but selecting them is a
**human decision** per DESIGN §0.1 — not made here.

## `ollama list` (verbatim, 2026-07-13)

```
NAME           ID              SIZE      MODIFIED
qwen3.5:2b     324d162be6ca    2.7 GB    4 weeks ago
qwen3.5:4b     2a654d98e6fb    3.4 GB    4 weeks ago
lfm2.5:8b      9cf756159fc2    5.2 GB    4 weeks ago
llama3.1:8b    46e0c10c039e    4.9 GB    4 weeks ago
qwen3.5:9b     6488c96fa5fa    6.6 GB    4 weeks ago
```

Ollama version: `0.30.7`.

## Qwen3 thinking flag (for cycle T3)

DESIGN §2 notes Qwen3 is a hybrid thinking model and that thinking is pure latency for these
extraction-class transforms — it must be **disabled**. Ollama exposes a `think: false` request
field for Qwen3 (older workaround: a `/no_think` tag in the prompt). **Verify the exact
current field name against the installed Ollama version's docs/behavior in cycle T3 and record
the finding here.** This is unverified in T1 (no generation code exists yet, and the bound
models are absent).
