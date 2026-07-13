# ADR 0005: Concurrency — single in-flight generation

**Status:** Accepted
**Date:** 2026-07-13

> Transcribed from DESIGN §2 (ADR-0005).

## Context

One 12GB card, one loaded model. Concurrent generations would contend for VRAM and compute,
degrading latency for everyone and risking out-of-memory with larger models.

## Decision

Serialize all generation behind an **asyncio semaphore of size 1** — a single in-flight
generation at a time. Requests **queue** up to `QUEUE_WAIT_S` (default 90s); on timeout the
service returns **503** with `reason: "busy"`. No multi-model juggling in v1.

## Consequences

- Predictable, fair behavior on a single GPU; no VRAM thrash.
- Under load, callers see queueing latency (`meta.queued_ms`) and, past the timeout, a
  machine-distinguishable `503 busy` — which fits both consumption patterns (Brickfeed
  fails over; Scriptorium pauses and resumes; DESIGN §8).
- Throughput is bounded by one generation at a time; acceptable for the batch/pipeline
  workloads this service serves.
- The semaphore is service-wide state, wired in cycle T3 alongside the real Ollama client.
