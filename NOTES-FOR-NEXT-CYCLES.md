# Notes for next cycles

Out-of-scope discoveries parked here during T1 (not implemented — scope fence).

## BLOCKER for T3+ — bound models are not installed
- `qwen3:8b` and `qwen3:0.6b` (DESIGN §2 bindings) are **absent** on the box. Box has
  `qwen3.5:2b/4b/9b`, `lfm2.5:8b`, `llama3.1:8b`. See `docs/models.md` for the full record and
  the human action required. **No substitution was made** (DESIGN §0.1). T2 (FakeLLM only) is
  unaffected; T3 (real Ollama generation) and T4+ cannot land until models are resolved.

## T3 — verify the Qwen3 "disable thinking" field name
- DESIGN §2 says to disable Qwen3 thinking (`think: false`, older workaround `/no_think`).
  Verify the exact field name against the installed Ollama version (0.30.7) and record in
  `docs/models.md`. Unverified in T1 (no generation code, models absent).

## T2/T3 — modules deliberately NOT created in T1
- `llm.py`, `registry.py`, `pipeline.py`, `budget.py` are later-cycle scope and were left
  uncreated on purpose (not even as empty stubs). The `/health` Ollama probe lives in
  `tts/health.py` and is intentionally separate from the future `OllamaClient` (T3).

## From T2 — conventions later cycles must follow

- **Prompt-template convention (T4-T6).** `render_messages` (in `pipeline.py`) renders the
  transform's `template` (Jinja2), splits on the first `USER:` marker into system/user
  messages, strips a leading `SYSTEM:`, and replaces the literal token `{common framing}` with
  the §7 constant. **Write each §7 transform template verbatim in this SYSTEM/USER form** and it
  drops in unchanged. Templates already use `| tojson` / `| join` / `| default` — all Jinja2
  built-ins, no filter registration needed.
- **Validators are top-level-field only (add nested for T5).** `validators.py` factories index
  `output[field]`. cast-mentions' `no_empty_strings(mentions[].name)` needs array-of-objects
  path traversal — extend the validator library (or add a path helper) when T5 lands; unit-test
  the nested case.
- **T3 wiring seam.** The route returns `503 model_unavailable` while `app.state.llm is None`.
  T3 sets `app.state.llm = OllamaClient(...)` at startup and maps Ollama-level failures to
  `503 model_unavailable` inside/around the client. The single-slot `app.state.gen_semaphore`
  already exists; `queue_wait_s` comes from settings. `FakeLLMClient` supports async callables
  (used by the sleepy queue-timeout test) — reuse that pattern for any T3 timing tests.
- **`meta.input_tokens_est`** is the estimate of the *post-truncation* text actually sent (equals
  the original when not truncated). Keep this if adding fields.

## Housekeeping
- `text-transform-service-design.md` (lowercase) is the superseded v1 draft; it sits untracked
  in the repo root. Left as-is (not created by this cycle). Consider removing it once the v2
  `text-transform-service-DESIGN.md` is confirmed canonical.
- `tts/app.py` resolves `Settings` once at import. Tests override via `app.state.settings`.
  When auth/logging land (T7), consider a lifespan-based settings/state wiring.
- Starlette TestClient emits a `StarletteDeprecationWarning` about httpx; harmless, no action.
