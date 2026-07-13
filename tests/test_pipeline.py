"""Tests for the request pipeline (DESIGN §3, §4).

These call ``run_transform`` directly with inline Transforms and a FakeLLM, so every
error code in the §4 table is reachable without HTTP or a real model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tts.llm import FakeLLMClient
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


def _sem() -> asyncio.Semaphore:
    return asyncio.Semaphore(1)


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
        await run_transform(t, "text", {}, fake, _sem(), 5.0)
    assert exc.value.status == 400
    assert exc.value.code == "bad_options"


async def test_over_budget_reject_is_413():
    t = _transform(input_budget=1, over_budget="reject")
    fake = FakeLLMClient(['{"echo": "x"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "one two three four five", {}, fake, _sem(), 5.0)
    assert exc.value.status == 413
    assert exc.value.code == "over_budget"
    # No generation happened.
    assert fake.calls == []


async def test_over_budget_truncate_sets_meta_truncated():
    t = _transform(input_budget=3, over_budget="truncate", truncation_strategy="head")
    text = "alpha alpha alpha alpha\n\nbravo bravo bravo bravo"
    fake = FakeLLMClient(['{"echo": "x"}'])
    result = await run_transform(t, text, {}, fake, _sem(), 5.0)
    assert result["meta"]["truncated"] is True


async def test_retry_invalid_then_valid_success_with_temp_bump():
    t = _transform(temperature=0.3, temp_bump=0.15, retries=1)
    fake = FakeLLMClient(["not json", '{"echo": "ok"}'])
    result = await run_transform(t, "text", {}, fake, _sem(), 5.0)

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
        await run_transform(t, "text", {}, fake, _sem(), 5.0)
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    assert len(exc.value.detail["reasons"]) == t.retries + 1


async def test_validator_failure_drives_retry_then_422():
    # Schema-valid but a validator rejects (contains a newline); exhausts retries -> 422.
    t = _transform(retries=2, validators=(banned_substrings("echo", ["\n"]),))
    fake = FakeLLMClient(['{"echo": "line1\\nline2"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, _sem(), 5.0)
    assert exc.value.status == 422
    assert len(exc.value.detail["reasons"]) == 3
    assert all("banned substring" in r for r in exc.value.detail["reasons"])


async def test_queue_timeout_is_503_busy():
    t = _transform()
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # hold the only slot so the request must queue
    fake = FakeLLMClient(['{"echo": "x"}'])
    with pytest.raises(TransformError) as exc:
        await run_transform(t, "text", {}, fake, sem, queue_wait_s=0.01)
    assert exc.value.status == 503
    assert exc.value.code == "busy"


async def test_sleepy_generation_makes_a_concurrent_request_queue_then_time_out():
    # Two concurrent requests, one slot. The first holds it while its fake sleeps; the
    # second queues and times out -> 503 busy (the semaphore is genuinely in place).
    t = _transform()
    sem = asyncio.Semaphore(1)

    async def slow(messages, schema, params):
        await asyncio.sleep(0.2)
        return '{"echo": "x"}'

    def run(queue_wait):
        return run_transform(t, "text", {}, FakeLLMClient(slow), sem, queue_wait)

    first = asyncio.create_task(run(5.0))
    await asyncio.sleep(0.02)  # let the first task grab the slot
    with pytest.raises(TransformError) as exc:
        await run(queue_wait=0.01)
    assert exc.value.status == 503
    result = await first
    assert result["output"] == {"echo": "x"}


# ---- success meta ----------------------------------------------------------------

async def test_success_meta_has_all_section_4_fields():
    t = _transform()
    fake = FakeLLMClient(['{"echo": "hi"}'])
    result = await run_transform(t, "hello world", {}, fake, _sem(), 5.0)

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
