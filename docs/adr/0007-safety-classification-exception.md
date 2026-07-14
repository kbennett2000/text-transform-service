# ADR 0007: Conditional exception for safety-relevant classification

**Status:** Accepted (product owner)
**Date:** 2026-07-14

## Context

DESIGN §1 excludes "safety-relevant classification" from the service charter. Brickfeed's
`opinion-gate` (`docs/requests/brickfeed-2026-07.md` §2) is a fail-closed editorial harm filter
— it decides whether a candidate news story is acceptable source material for a lighthearted
satirical opinion section, excluding anything centering tragedy, violence, death, disaster
casualties, or victims. That decision is safety-load-bearing, so under §1 as written the
transform was HELD out of charter in cycle T9, pending this product-owner call.

## Decision

§1's blanket exclusion of safety-relevant classification is amended to a **conditional** one. A
safety-relevant classifier may be registered on the service **iff** all three conditions hold:

1. **Its output schema is a closed enum verdict that includes an explicit `uncertain` value** —
   no free text drives the decision.
2. **The module docstring documents the caller's fail-closed obligation:** every
   transport/validation error AND every `uncertain` verdict must be treated as the safe outcome
   (exclude). TTS itself remains fail-loud and implements no fallback.
3. **Scope is limited to editorial gating of machine-selected public content with periodic human
   audit expected of the consumer.** This ADR does not authorize moderation of user-generated
   content or any decision lacking a human audit path.

## Consequences

`opinion-gate` is in-charter under these conditions and ships in cycle T10. Future classifiers
must satisfy all three conditions or be held. The service keeps its fail-loud posture: it never
substitutes a default verdict on error — the *caller* implements fail-closed by mapping any
4xx/5xx (and any `uncertain` verdict, and any missing/duplicate id) to "exclude." The `uncertain`
enum value exists precisely so the model can decline to guess without the service inventing a
safe default. This exception is deliberately narrow; it does not reopen the §1 exclusions on
long-form voiced generation (which continues to keep `opinion-piece` out of charter) or on
general-gateway use.
