# Scriptorium System — Overview

**Status:** Approved for build — 2026-07-13
**Owner:** Kris Bennett / Twelve Rocks LLC
**Executor:** Claude Code (Sonnet), cycle-dispatched. Browser Claude is product owner/strategist.
**Working name:** *Scriptorium* (the reader app + bakery). Rename is trivial until the repo exists; do not bikeshed it mid-build.

This document orients the executor. The authoritative detail lives in the four documents it indexes. **Read this first, then the DESIGN doc for the repo you are working in, then the BUILD-PLAN cycle you were dispatched.**

---

## 1. What we are building

An illuminated-book pipeline and offline-first reader:

1. Ingest a book (Project Gutenberg fetch, or user-supplied .txt/.md).
2. Paginate into logical pages (400–800 words).
3. LLM passes (local, on the GPU box): extract the cast, thread a scene ledger page-to-page, score pages for visual salience, derive illustration prompts for selected pages.
4. Human review gate: approve/edit the shot list and cast descriptions in an admin UI.
5. Render plates via the existing `imagegen-service` (SDXL, per-book art style).
6. Publish an **immutable bundle**. Readers (phone/desktop) check bundles out and read fully offline with highlights, notes, bookmarks, per-user sync.

## 2. Component map

| Component | Box | Repo | Status |
|---|---|---|---|
| `text-transform-service` | 5070 dev PC (Ubuntu, RTX 5070 12GB) | `text-transform-service` (new) | Build now |
| `imagegen-service` | 5070 dev PC | existing (Chronicle's) | **No changes for v1** |
| Scriptorium server (bakery, library, sync, admin UI) | i5-3540 LAN server (Ubuntu Server) | `scriptorium` (new, monorepo `server/`) | Build now |
| Scriptorium reader (client) | User devices (Android/iOS via Capacitor, desktop via installable PWA) | `scriptorium` (monorepo `reader/`) | Build now |

Two new repos total. `imagegen-service` is consumed as-is; its client in Scriptorium is written against an interface and verified against the real API in cycle S10.

## 3. Data flow

```
Gutenberg / user file
        │
        ▼
┌─ i5 server ──────────────────────────────────────────────┐
│ ingest → paginate → [P1..P5 LLM phases]──HTTP──► 5070:    │
│                                          text-transform-  │
│   selection (deterministic, CPU)         service (Ollama) │
│        │                                                  │
│   REVIEW GATE (admin UI, human)                           │
│        │                                                  │
│   render phase ──────────────HTTP──────► 5070:            │
│        │                                 imagegen-service │
│   publish immutable bundle                                │
│        │                                                  │
│   library / checkout / sync APIs                          │
└────────┼──────────────────────────────────────────────────┘
         ▼  (checkout: manifest + files, hash-verified)
   Reader devices — fully offline after checkout
         ▲  (opportunistic sync: annotations, positions)
```

## 4. Build order and dependencies

```
text-transform-service:  T1 → T2 → T3 → T4 → T5 → T6 → T7        (T8 deferred: Brickfeed bench)
scriptorium server:      S1 → S2 → S3 → S4 ─┬→ S5 → S6 → S7 → S8 → S9 → S10 → S11 → S12
                                            │
                        (S5/S6/S8 need T5/T6 live, or recorded fixtures — fixtures preferred for tests)
scriptorium reader:      R1 → R2 → R3 → R4 → R5
                        (R1 can start after S3 against a hand-built fixture bundle; needs S11 to go live)
milestone:               M1 — First Full Bake (The Time Machine, PG #35)
```

T1–T4 and S1–S4 have no cross-dependencies and can interleave freely. Everything LLM-touching in Scriptorium develops against **recorded fixtures** so tests run on the i5 (no GPU); live integration is exercised via `gpu`-marked tests and the M1 milestone.

## 5. Load-bearing invariants

These are system-wide. Each is restated with full rationale in the relevant DESIGN doc and gets an ADR in its repo. Violating any of these is a bug even if everything appears to work.

1. **Immutability:** after publish, page text, structure, and bundle ids never change. Revisions are additive only. (Annotation anchors must never rot.)
2. **Causality / no spoilers:** any LLM pass that produces reader-visible content for page N sees only page N and state established on pages ≤ N. Selection may look ahead but only at numeric scores, never content.
3. **Zero online at read time:** after checkout, the reader makes no network calls to function — no internet, no LAN, no server process. No CDN fonts, no CDN scripts, ever. All assets vendored.
4. **Review gate:** no plate is rendered before a human approves the shot list. The pipeline structurally cannot skip P6.
5. **GPU phase exclusivity:** the i5 orchestrator sequences GPU work — the LLM is explicitly unloaded before any render phase starts, and LLM phases never run while a render is in flight. The 5070 never has to arbitrate.
6. **Fallback is time, not money:** Scriptorium holds no API keys. If the GPU box is asleep or the transform service errors, bakes pause and resume; they never fail over to a paid provider.
7. **Everything resumable:** every bake phase checkpoints per-unit; killing any process at any point loses at most one unit of work.
8. **Reproducibility pinning:** exact model tags, service versions, style ids, and prompt-template versions used by a bake are recorded in the bundle's `meta.json`.

## 6. Document index

| File | Contents |
|---|---|
| `text-transform-service-DESIGN.md` | v2 design: decisions locked, transform abstraction, API, full transform catalog with schemas and v0 prompt templates |
| `text-transform-service-BUILD-PLAN.md` | Cycles T1–T8 with scope, steps, acceptance criteria |
| `scriptorium-DESIGN.md` | Bundle format, ingestion, pagination, bake pipeline, selection engine, styles, server, sync, reader, multi-user |
| `scriptorium-BUILD-PLAN.md` | Cycles S1–S12, R1–R5, milestone M1 |
| `cc-kickoff-tts-cycle-01.md` | Ready-to-paste Claude Code kickoff for the first cycle |

## 7. Glossary

- **Bundle** — the immutable published artifact for one book (text, cast, images, manifest).
- **Logical page** — a 400–800-word baked unit; the anchor unit for plates and annotations. Distinct from a *screen* page (the reader reflows within logical pages).
- **Plate** — one rendered illustration attached to one logical page.
- **Ledger** — the rolling scene state (location, time, who's present) threaded page-to-page during the bake.
- **Shot list** — the set of (page, prompt) pairs selected for rendering, as shown in the review gate.
- **Checkout** — a reader downloading a bundle to a device, hash-verified, for offline use.
- **Transform** — a named text→JSON operation registered in `text-transform-service`.
