# ADR 0008: Concurrency-burst reliability — bounded queue, reload-on-demand, readiness

**Status:** Accepted
**Date:** 2026-07-14

> Extends ADR-0005 (single in-flight generation). Cycle T14.

## Context

A consumer that fires several transforms concurrently was getting intermittent 503s under
load: many `503 busy` during a burst, interleaved with `503 model_unavailable` and repeated
`POST /v1/models/unload`. ADR-0005 already serializes generation behind one slot and queues
up to `QUEUE_WAIT_S`, so the `busy` storm was a *symptom*: when another on-box consumer (or
the Scriptorium orchestrator, which legitimately unloads before render phases — system-overview
§5 GPU-phase exclusivity) evicts the model mid-workload, the next generation must cold-reload
while racing the unload → `model_unavailable`, and the reload latency backs the queue up past
`QUEUE_WAIT_S` → `busy`.

Separately, `/health` reports `status:"ok"` whenever Ollama's `/api/ps` answers, even with
`models_loaded:[]`, so a caller cannot distinguish "up but no model resident" from "ready to
serve".

Constraints: the deliberate `/v1/models/unload` mechanism must stay intact (GPU-phase
exclusivity is the caller's job); `/health`'s §4 shape is a contract with two consumers;
`OLLAMA_KEEP_ALIVE` stays service-config-only (DESIGN §5); models are never substituted.

## Decision

We will make a well-behaved concurrent caller stop getting 503s via three changes, without
disabling unload or changing `keep_alive`:

1. **Bounded generation queue.** The single slot moves into a `GenerationGate` that keeps the
   ADR-0005 time bound (`QUEUE_WAIT_S`) and adds an optional depth bound `MAX_QUEUE_DEPTH`
   (default `0` = unbounded, i.e. pre-T14 behavior). When set, a request arriving with the
   queue already full fast-fails `503 busy` immediately instead of waiting out the full
   timeout. `busy` semantics and shape are unchanged.

2. **Reload-on-demand + serialized unload.** The pipeline calls `LLMClient.ensure_loaded(model)`
   inside the slot before generating, so a prior unload (or idle keep-alive expiry) can't leave
   a caller with `model_unavailable` — it reloads and succeeds (slower). `POST /v1/models/unload`
   acquires the same slot, so an eviction can never race an in-flight generation. Genuine
   backend-down still maps to `503 model_unavailable`.

3. **Readiness signal.** A new unauthenticated `GET /ready` reports `ready` true iff Ollama is
   reachable **and** the primary model (`TTS_PRIMARY_MODEL`, default the production working
   binding) is resident. `/health` gains an additive `ready` boolean; its `status` semantics
   are unchanged (still `ok` iff Ollama answers), so the §4 contract does not break.

Access to `/v1/models/unload` is **not** newly gated — the self-heal (reload-on-demand + slot
serialization) makes an ill-timed unload non-fatal, which was the reason gating was considered.

## Consequences

- A concurrent burst serializes through the one slot and all requests succeed (each carries its
  `meta.queued_ms`), instead of some timing out; verified live (10/10 → 200, 0 busy).
- An eviction mid-workload self-heals: the next transform reloads the model transparently.
- Operators get a true readiness probe (`/ready`, and `/health.ready`) distinct from liveness.
- Cost: reload adds latency to the first request after an eviction; unload may wait up to
  `QUEUE_WAIT_S` behind a long generation (then `503 busy`, retried by the caller between phases).
- New knobs: `MAX_QUEUE_DEPTH` (default off) and `TTS_PRIMARY_MODEL` (default `qwen3.5:9b`).
- Escape hatch: `MAX_QUEUE_DEPTH=0` restores exact pre-T14 queue behavior; `ensure_loaded` may be
  a no-op for backends whose generation auto-loads (as `FakeLLMClient`'s is).
