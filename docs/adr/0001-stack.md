# ADR 0001: Stack — Python 3.12 + FastAPI + uv

**Status:** Accepted
**Date:** 2026-07-13

> Transcribed from DESIGN §2 (ADR-0001). The decision and rationale are pre-decided there;
> this record exists so the choice is greppable in the repo, not to re-open it.

## Context

The service needs a small HTTP API in front of local LLM inference. The house already
runs this pattern (Concord, radio-server), so tooling and idioms are established.

## Decision

Build on **Python 3.12 + FastAPI + uv**:

- **Pydantic v2** for request/response models.
- **`httpx`** async client for talking to Ollama.
- **`jinja2`** for prompt templates.
- **`ruff`** + **`pytest`** for lint and tests.
- **No Docker for v1** — a bare `uv` venv plus a systemd unit (see DESIGN §9).

## Consequences

- Matches the existing house stack; low ramp-up, shared conventions.
- `uv` gives fast, reproducible env management and a committed lockfile.
- No container isolation in v1; deployment is rsync-to-`/opt` + `uv sync` + systemd (T7).
- Async throughout (FastAPI + httpx) fits the single-in-flight generation model (ADR-0005).
