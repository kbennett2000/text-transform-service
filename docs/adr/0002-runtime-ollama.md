# ADR 0002: Runtime — Ollama

**Status:** Accepted
**Date:** 2026-07-13

> Transcribed from DESIGN §2 (ADR-0002).

## Context

Local inference on a single 12GB RTX 5070. We need model version management, an HTTP API,
grammar-constrained (schema-constrained) decoding so output format drift is structurally
impossible, and control over model unloading so the GPU can be handed to the imagegen
service between phases.

## Decision

Use **Ollama** as the inference runtime, chosen over a raw `llama.cpp` server. Ollama provides:

- model pull / version management,
- an HTTP API,
- **JSON-schema structured outputs** — it compiles the schema to a llama.cpp grammar
  internally, giving grammar-constrained decoding without hand-writing GBNF,
- per-request `keep_alive` for unload control.

Raw `llama.cpp` would buy marginally tighter grammar control at a large assembly cost.

**Escape hatch:** the Ollama client lives behind an internal `LLMClient` protocol
(DESIGN §6). If schema fidelity ever proves insufficient, a `llama.cpp`-server client can
be swapped in without touching any transform.

## Consequences

- We depend on Ollama's structured-output implementation for constrained decoding; the
  `LLMClient` seam contains that dependency.
- Model tags are Ollama library tags and can move over time — exact resolved tags are
  recorded per build in `docs/models.md` (DESIGN §0.1); the executor never silently
  substitutes a model.
- Unload is performed via Ollama's supported mechanism (a minimal generate with
  `keep_alive: 0`), exposed to callers as `/v1/models/unload` (DESIGN §4).
