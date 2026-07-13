# CLAUDE.md — text-transform-service

LAN-only, credential-free local-LLM transform service (FastAPI + Ollama) on the RTX 5070 box, port 8712. Named transforms: text in → schema-constrained JSON out. Consumers: Brickfeed News (failover posture) and Scriptorium (pause posture). Not a general LLM gateway. Never internet-facing.

## Read before working
1. `system-overview.md` — system context + binding invariants
2. `text-transform-service-DESIGN.md` — the spec; §7 is the transform catalog
3. `text-transform-service-BUILD-PLAN.md` — §0 discipline + your current cycle only

You execute **one cycle per session**, named in your kickoff. Plan mode first: restate scope, files, tests, ambiguities. Ambiguity → ask, don't improvise.

## Commands
```
just dev        # uvicorn --reload on :8712
just test       # pytest -m "not gpu"  (must pass anywhere)
just test-gpu   # pytest -m gpu        (only on the 5070; check nvidia-smi first)
just lint       # ruff check .
```

## Hard rules
- **Scope fence.** Only the current cycle's "In scope." Discoveries → `NOTES-FOR-NEXT-CYCLES.md`, never code.
- **ADRs are transcription.** Decisions live in DESIGN §2. Don't re-litigate; don't invent new ones without asking.
- **Never assert exact LLM wording in tests.** Schema, shape, bounds only.
- **Error codes are API.** The §4 taxonomy (400/401/404/413/422/503) is a contract with two consumers. Changing a code is a breaking change.
- **`/health` never 500s.** Ollama down = `degraded` data.
- **Never substitute models.** Bindings live in `docs/models.md`; missing model → stop and report.
- **Templates are versioned.** Any prompt-template change bumps that transform's `version` and gets a CYCLE-LOG line.
- FakeLLM for all non-gpu tests; real generation only behind `-m gpu`.

## Layout
`src/tts/` — `app.py` routes · `pipeline.py` request pipeline · `llm.py` LLMClient/Ollama/Fake · `budget.py` token estimate + truncation · `registry.py` Transform dataclass · `transforms/` one module per transform.

## Done means
ruff clean · non-gpu tests green · cycle acceptance checklist satisfied · `CYCLE-LOG.md` entry (id, date, shipped, deviations) · commits prefixed `T{n}:` · README updated if behavior changed.
