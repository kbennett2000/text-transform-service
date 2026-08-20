# ADR 0009: GPU tenancy lock — server-side exclusivity via a shared flock lease

**Status:** Accepted
**Date:** 2026-08-20

> Supersedes the caller-side GPU-phase-exclusivity clause of ADR-0008 (Constraints, "GPU-phase
> exclusivity is the caller's job"). Leaves ADR-0008's bounded-queue / reload-on-demand /
> readiness decisions intact. Cycle T22.

## Context

One RTX 5070 (12 GB) hosts two GPU tenants: this service (Ollama-backed, :8712) and
imagegen-service (:8189 → ComfyUI :8188). When ComfyUI keeps a checkpoint warm (~6.9 GB), the
9B opinion model cannot stay resident, so Ollama evicts and cold-reloads it per call
(~27–30 s/gen) and a burst never drains inside `QUEUE_WAIT_S` → the `status=0` / `503 busy` /
`413 over_budget` storms documented in `docs/models.md` (T21). Trying to keep both models
co-resident is the wrong fight — the card cannot hold both comfortably, so they thrash.

Until now exclusivity was declared **caller-side**: ADR-0008 framed it as "the caller's job",
`docs/models.md` (Ops requirement) and DESIGN §9 (GPU coexistence) told the brickfeed cron to
POST ComfyUI `/free` before its TTS window, and system-overview §5 leaned on the orchestrator
sequencing GPU phases. The owner has **reversed** this: a client should never drive another
service's model lifecycle, and callers should not have to know a GPU exists. Exclusivity moves
server-side, into the two services, coordinated through one shared thing.

Constraints: no new daemon/broker (nothing new to deploy, monitor, or wedge); crash-safety
without a TTL/heartbeat; must not weaken ADR-0005's `Semaphore(1)` or the `/v1/models/unload`
route; no cross-service HTTP (each tenant frees its own VRAM); and — because the lock is a
throughput optimization, not a safety gate — a missing/broken lockfile must never block GPU work.

## Decision

We will enforce GPU exclusivity server-side with one **advisory file lock** (`flock(2)`,
`LOCK_EX`) on a well-known lockfile both tenants open — `/run/gpu-tenant.lock` (falling back to
`/var/lock/gpu-tenant.lock`). Every GPU operation runs inside a **lease**:
acquire → ensure model → **drain** → **free** → release.

- **Hold-and-drain.** This service takes the flock when it goes idle→busy and holds it across
  successive `Semaphore(1)` slots while work keeps arriving, so a burst of ~30 story-cover calls
  loads the model **once** and amortizes it over the whole burst. It keeps the lock a short
  `GPU_LOCK_IDLE_GRACE_S` after the last queued item (bridging brickfeed's generate→opinions gap
  without a reload).
- **Free before release.** On going idle (no in-flight generation, no waiter) for the grace
  window — or on hitting `GPU_LOCK_MAX_HOLD_S` — the service unloads its model from VRAM (the
  existing Ollama `keep_alive:0` path) **and only then** drops the flock, so the next tenant
  always acquires into clear VRAM.
- **MAX_HOLD fairness, non-preemptive.** If work is still queued at `GPU_LOCK_MAX_HOLD_S`, the
  current generation finishes, then the service frees, releases, and re-acquires at the back of
  the kernel's wait queue — so a hammering burst on one tenant can't starve the other. A running
  generation is never interrupted.
- **Fail-open.** If the lockfile can't be opened or locked, or the acquire exceeds
  `GPU_LOCK_ACQUIRE_TIMEOUT_S`, the service logs a warning and proceeds **without** the lock.
- **Crash-safety is free.** The kernel releases `flock` when the holding process dies — no TTL,
  no heartbeat, no reaper. `flock`'s wait queue also gives rough-FIFO fairness.

The lease is layered *above* the in-process `Semaphore(1)`: the flock is acquired inside the slot
(the slot-winner is the sole would-be acquirer, so no in-process acquire race), held across slots,
and freed by a separate idle timer. `/v1/models/unload` stays fully functional but is no longer
what callers rely on for exclusivity; a manual unload also drops the lease if held. This service
frees only its **own** VRAM (Ollama unload) — it never touches ComfyUI; imagegen frees ComfyUI on
its side. The lockfile is the only coordination point.

## Consequences

- A burst acquires the lock once, loads once, drains, frees, releases — no per-call reloads; and
  while imagegen holds the lock this service blocks on acquire, then proceeds into clear VRAM (no
  eviction storm, no contention-driven 413/503). Callers change nothing.
- The two tenants serialize on the GPU: while imagegen holds the lease for a long render (a Wan
  video can be ~20 min), TTS calls block on acquire and may hit their own `503 busy` / queue-wait.
  That is correct — the GPU is genuinely busy. Mitigation is scheduling, not preemption
  (don't launch long renders during a TTS-heavy window); out of scope for the lock.
- ComfyUI goes cold between imagegen batches (it frees on release); the next batch pays a one-time
  reload. Hold-and-drain keeps that to once per batch. This is the deliberate trade — a
  warm-always ComfyUI is exactly what starves TTS today.
- New knobs (all optional, fail-open): `GPU_LOCK_ENABLED`, `GPU_LOCK_PATH`, `GPU_LOCK_MAX_HOLD_S`,
  `GPU_LOCK_IDLE_GRACE_S`, `GPU_LOCK_ACQUIRE_TIMEOUT_S`. `OLLAMA_KEEP_ALIVE` is unchanged (now
  harmless; the lease drives unload on idle).
- Coordination contract with imagegen-service: the lockfile **path**, the **free-before-release
  ordering**, and the **fail-open semantics** must match byte-for-byte; timing knobs may differ.
- Escape hatch: `GPU_LOCK_ENABLED=false` restores exact pre-T22 behavior (no lease; exclusivity
  back to the caller / `/v1/models/unload`). A broker with a real queue + per-request priorities
  is a possible later upgrade; not required for correctness and not built now.
