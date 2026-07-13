# ADR 0004: Style-wrapping is caller-side

**Status:** Accepted
**Date:** 2026-07-13

> Transcribed from DESIGN §2 (ADR-0004).

## Context

Multiple consumers want image prompts in different visual styles (Brickfeed's toy-brick
treatment, Scriptorium's per-book art styles). If the service baked style into its output,
every consumer would fight the service's opinion and transforms would need style parameters.

## Decision

All transforms return **neutral-subject** content. Visual style — medium, palette, artist
tags, and any house treatment — is applied **by the caller**, downstream of this service.

**Exception that is *not* style:** the `illustration-prompt` transform weaves provided
**character visual descriptions** into its output, because character identity is *subject*,
not style.

## Consequences

- Transform outputs are reusable across consumers with different styles.
- Callers own their style layer; the service stays a pure subject/content function.
- Validators actively guard against style/medium leakage (e.g. banned substrings like
  "watercolor", "oil painting" in `illustration-prompt`) — their appearance in output is
  drift, not a feature.
- Character descriptions are the one subject-bearing input threaded through, and are passed
  explicitly by the caller via transform `options`.
