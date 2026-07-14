"""Tests for the request pipeline (DESIGN §3, §4).

These call ``run_transform`` directly with inline Transforms and a FakeLLM, so every
error code in the §4 table is reachable without HTTP or a real model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tts.concurrency import GenerationGate
from tts.llm import FakeLLMClient, LLMBackendError
from tts.pipeline import TransformError, render_messages, run_transform
from tts.registry import Transform
from tts.validators import banned_substrings

ECHO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["echo"],
    "properties": {"echo": {"type": "string"}},
}


def _transform(**overrides) -> Transform:
    base = dict(
        name="t",
        version="0.1.0",
        template='USER:\n{{ text }}',
        model="qwen3:0.6b",
        output_schema=ECHO_SCHEMA,
    )
    base.update(overrides)
    return Transform(**base)


def _gate(queue_wait_s: float = 5.0, max_queue_depth: int = 0) -> GenerationGate:
    """A fresh single-slot generation gate for a directly-driven pipeline test."""
    return GenerationGate(queue_wait_s=queue_wait_s, max_queue_depth=max_queue_depth)


# ---- render_messages -------------------------------------------------------------

def test_render_messages_splits_system_and_user_and_injects_framing():
    template = "SYSTEM: {common framing}\nYou do a thing.\n\nUSER:\nText: {{ text }}"
    messages = render_messages(template, "hello", {})
    assert messages[0]["role"] == "system"
    assert "precise text-processing function" in messages[0]["content"]
    assert "You do a thing." in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Text: hello"


def test_render_messages_without_markers_uses_common_framing_as_system():
    messages = render_messages("just {{ text }}", "hi", {})
    assert messages[0]["role"] == "system"
    assert "precise text-processing function" in messages[0]["content"]
    assert messages[1]["content"] == "just hi"


# ---- error taxonomy --------------------------------------------------------------

async def test_bad_options_is_400():
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    t = _transform(options_schema=schema)
    fake = FakeLLMClient(['{"echo": "x"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _gate())
    assert exc.value.status == 400
    assert exc.value.code == "bad_options"


async def test_over_budget_reject_is_413():
    t = _transform(input_budget=1, over_budget="reject")
    fake = FakeLLMClient(['{"echo": "x"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "one two three four five", {}, fake, _gate())
    assert exc.value.status == 413
    assert exc.value.code == "over_budget"
    # No generation happened.
    assert fake.calls == []


async def test_over_budget_truncate_sets_meta_truncated():
    t = _transform(input_budget=3, over_budget="truncate", truncation_strategy="head")
    text = "alpha alpha alpha alpha\n\nbravo bravo bravo bravo"
    fake = FakeLLMClient(['{"echo": "x"}'])
    result = await run_transform(t, text, {}, fake, _gate())
    assert result["meta"]["truncated"] is True


async def test_retry_invalid_then_valid_success_with_temp_bump():
    t = _transform(temperature=0.3, temp_bump=0.15, retries=1)
    fake = FakeLLMClient(["not json", '{"echo": "ok"}'])
    result = await run_transform(t, "text", {}, fake, _gate())

    assert result["output"] == {"echo": "ok"}
    assert result["meta"]["attempts"] == 2
    # First attempt at base temp, second attempt bumped.
    assert fake.calls[0].params["temperature"] == pytest.approx(0.3)
    assert fake.calls[1].params["temperature"] == pytest.approx(0.45)
    # The output_schema is passed through for constrained decoding.
    assert fake.calls[0].format_schema == ECHO_SCHEMA


async def test_always_invalid_is_422_with_reasons_len_retries_plus_1():
    t = _transform(retries=1)
    fake = FakeLLMClient(["nope"])  # always invalid JSON
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _gate())
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    assert len(exc.value.detail["reasons"]) == t.retries + 1


async def test_computed_num_ctx_is_threaded_into_llm_params():
    # T12: the pipeline passes the transform's num_ctx into the LLM params so Ollama sizes
    # its context window. Left unset it is the computed default input_budget+num_predict+1024.
    t = _transform(input_budget=8000, num_predict=5120)
    fake = FakeLLMClient(['{"echo": "ok"}'])
    await run_transform(t, "text", {}, fake, _gate())
    assert fake.calls[0].params["num_ctx"] == 8000 + 5120 + 1024  # == 14144


async def test_num_ctx_override_is_threaded_into_llm_params():
    t = _transform(input_budget=8000, num_predict=5120, num_ctx=4096)
    fake = FakeLLMClient(['{"echo": "ok"}'])
    await run_transform(t, "text", {}, fake, _gate())
    assert fake.calls[0].params["num_ctx"] == 4096


async def test_validation_failure_422_carries_raw_snippet():
    # T12 observability: on total validation failure the 422 detail surfaces a bounded
    # snippet of the last raw output — the signature of context-truncated (empty/garbage)
    # generation is otherwise invisible. Additive to the existing `reasons` key.
    t = _transform(retries=0)
    fake = FakeLLMClient(["{ truncated garbage no close"])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _gate())
    assert exc.value.status == 422
    assert exc.value.detail["reasons"]  # unchanged contract
    assert exc.value.detail["raw_snippet"] == "{ truncated garbage no close"


async def test_raw_snippet_is_bounded_to_300_chars():
    t = _transform(retries=0)
    fake = FakeLLMClient(["x" * 5000])  # long non-JSON blob
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _gate())
    assert len(exc.value.detail["raw_snippet"]) == 300


async def test_validator_failure_drives_retry_then_422():
    # Schema-valid but a validator rejects (contains a newline); exhausts retries -> 422.
    t = _transform(retries=2, validators=(banned_substrings("echo", ["\n"]),))
    fake = FakeLLMClient(['{"echo": "line1\\nline2"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _gate())
    assert exc.value.status == 422
    assert len(exc.value.detail["reasons"]) == 3
    assert all("banned substring" in r for r in exc.value.detail["reasons"])


# ---- soft validators / meta.warnings (T6, DESIGN §7.5) ---------------------------

def _always_warn(reason: str):
    """A soft validator that always returns a ``warn:`` finding."""

    def _v(output: dict) -> str | None:
        return f"warn:{reason}"

    return _v


async def test_soft_validator_warning_lands_in_meta_and_does_not_retry():
    # A `warn:` reason is recorded to meta.warnings and never fails/retries the request.
    t = _transform(validators=(_always_warn("soft note"),))
    fake = FakeLLMClient(['{"echo": "ok"}'])
    result = await run_transform(t, "text", {}, fake, _gate())
    assert result["output"] == {"echo": "ok"}
    assert result["meta"]["warnings"] == ["soft note"]
    assert result["meta"]["attempts"] == 1
    assert len(fake.calls) == 1  # single generation — a soft finding is not a retry trigger


async def test_no_warnings_omits_meta_warnings_key():
    # The common case: no soft findings -> meta has no `warnings` key (§4 shape unchanged).
    t = _transform()
    fake = FakeLLMClient(['{"echo": "ok"}'])
    result = await run_transform(t, "text", {}, fake, _gate())
    assert "warnings" not in result["meta"]


async def test_warning_from_a_discarded_attempt_does_not_surface():
    # Attempt 1 both warns AND hard-fails -> retried; its warning belongs to the rejected
    # generation and must not leak into the successful attempt's meta.
    def warn_on_bad(output: dict) -> str | None:
        return "warn:saw-bad" if output.get("echo") == "BAD" else None

    t = _transform(retries=1, validators=(warn_on_bad, banned_substrings("echo", ["BAD"])))
    fake = FakeLLMClient(['{"echo": "BAD"}', '{"echo": "ok"}'])
    result = await run_transform(t, "text", {}, fake, _gate())
    assert result["output"] == {"echo": "ok"}
    assert result["meta"]["attempts"] == 2
    assert "warnings" not in result["meta"]


async def test_queue_timeout_is_503_busy():
    t = _transform()
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # hold the only slot so the request must queue
    gate = GenerationGate(queue_wait_s=0.01, semaphore=sem)
    fake = FakeLLMClient(['{"echo": "x"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, gate)
    assert exc.value.status == 503
    assert exc.value.code == "busy"


async def test_sleepy_generation_makes_a_concurrent_request_queue_then_time_out():
    # Two concurrent requests share one gate (one slot). The first holds it while its fake
    # sleeps; the second queues and times out -> 503 busy (the slot is genuinely in place).
    t = _transform()
    gate = GenerationGate(queue_wait_s=0.05)

    async def slow(messages, schema, params):
        await asyncio.sleep(0.2)
        return '{"echo": "x"}'

    def run():
        return run_transform(t, "text", {}, FakeLLMClient(slow), gate)

    first = asyncio.create_task(run())
    await asyncio.sleep(0.02)  # let the first task grab the slot
    with pytest.raises(TransformError) as exc:
        await run()  # queues behind the first, exceeds the 0.05s wait -> busy
    assert exc.value.status == 503
    assert exc.value.code == "busy"
    result = await first
    assert result["output"] == {"echo": "x"}


async def test_second_concurrent_request_reports_positive_queued_ms():
    # Two concurrent requests, one slot, ample queue_wait: the second QUEUES behind the first
    # (whose fake sleeps) and then succeeds — proving meta.queued_ms > 0.
    t = _transform()
    gate = GenerationGate(queue_wait_s=5.0)

    async def slow(messages, schema, params):
        await asyncio.sleep(0.1)
        return '{"echo": "x"}'

    def run():
        return run_transform(t, "text", {}, FakeLLMClient(slow), gate)

    first = asyncio.create_task(run())
    await asyncio.sleep(0.02)  # let the first task grab the slot
    second = await run()  # queues until the first releases, then runs

    assert second["meta"]["queued_ms"] > 0
    first_result = await first
    assert first_result["meta"]["queued_ms"] >= 0


async def test_concurrent_burst_all_succeed_none_busy():
    # T14: N overlapping requests through one slot all succeed — none 503 busy. With fast
    # generation the queue drains well inside queue_wait_s; the burst just serializes.
    t = _transform()
    gate = GenerationGate(queue_wait_s=5.0)  # depth-unbounded (default)

    async def quick(messages, schema, params):
        await asyncio.sleep(0.01)
        return '{"echo": "x"}'

    def run():
        return run_transform(t, "text", {}, FakeLLMClient(quick), gate)

    results = await asyncio.gather(*(run() for _ in range(10)))

    assert len(results) == 10
    assert all(r["output"] == {"echo": "x"} for r in results)
    # The burst genuinely contended for the one slot: at least one request queued.
    assert max(r["meta"]["queued_ms"] for r in results) > 0


async def test_queue_full_fast_fails_busy_without_waiting():
    # T14: with max_queue_depth=1, a request arriving when the queue is already full
    # fast-fails 503 busy immediately rather than waiting out queue_wait_s (here 100s, so a
    # wait would hang the test). detail carries the depth bound.
    t = _transform()
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # hold the slot so nothing can run
    gate = GenerationGate(queue_wait_s=100.0, max_queue_depth=1, semaphore=sem)
    fake = FakeLLMClient(['{"echo": "x"}'])

    # One request fills the single queue slot (blocks waiting for the held sem).
    waiter = asyncio.create_task(run_transform(t, "text", {}, fake, gate))
    await asyncio.sleep(0.02)  # let it register as a waiter
    assert gate.waiters == 1

    # A second request sees the full queue and fast-fails immediately.
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, gate)
    assert exc.value.status == 503
    assert exc.value.code == "busy"
    assert exc.value.detail["max_queue_depth"] == 1

    # Release so the queued waiter can complete; no leak.
    sem.release()
    result = await waiter
    assert result["output"] == {"echo": "x"}


async def test_backend_error_is_503_model_unavailable_and_not_retried():
    # An LLMBackendError (Ollama down/errored) maps to 503 model_unavailable, fail-fast.
    class Down:
        def __init__(self):
            self.calls = 0

        async def ensure_loaded(self, model):
            return None

        async def chat(self, messages, schema, params):
            self.calls += 1
            raise LLMBackendError("connection refused", {"error": "refused"})

    t = _transform(retries=2)  # retries available, but infra failure must NOT retry
    down = Down()
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, down, _gate())
    assert exc.value.status == 503
    assert exc.value.code == "model_unavailable"
    assert down.calls == 1  # not retried


async def test_params_carry_model_binding():
    # The transform's model tag rides in params so the shared OllamaClient targets it.
    t = _transform(model="qwen3.5:9b")
    fake = FakeLLMClient(['{"echo": "ok"}'])
    await run_transform(t, "text", {}, fake, _gate())
    assert fake.calls[0].params["model"] == "qwen3.5:9b"


# ---- success meta ----------------------------------------------------------------

async def test_success_meta_has_all_section_4_fields():
    t = _transform()
    fake = FakeLLMClient(['{"echo": "hi"}'])
    result = await run_transform(t, "hello world", {}, fake, _gate())

    meta = result["meta"]
    assert set(meta) == {
        "transform",
        "transform_version",
        "model",
        "input_tokens_est",
        "truncated",
        "attempts",
        "latency_ms",
        "queued_ms",
    }
    assert meta["transform"] == "t"
    assert meta["transform_version"] == "0.1.0"
    assert meta["model"] == "qwen3:0.6b"
    assert meta["attempts"] == 1
    assert meta["truncated"] is False
    assert isinstance(meta["latency_ms"], int)
    assert isinstance(meta["queued_ms"], int)
    # Output survives schema validation and round-trips as JSON.
    assert json.loads(json.dumps(result["output"])) == {"echo": "hi"}
