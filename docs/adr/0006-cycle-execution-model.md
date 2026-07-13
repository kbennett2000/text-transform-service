# ADR 0006: Cycle execution model

> Renumbered from 0001 in cycle T1 so ADRs 0001–0005 could hold the service design
> decisions transcribed from DESIGN §2. Content unchanged.

- Execution runs as headless `claude -p`, one cycle per run.
- Fresh session each cycle; state lives in HANDOFF.md, CLAUDE.md, and ADRs — not resumed context.
- Work happens on a branch, never main. Human merges.
- When unsure or blocked: commit what exists, write the question into the issue, stop.
