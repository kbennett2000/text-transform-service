"""Unit tests for the `scene-update` transform (DESIGN §7.4), FakeLLM only.

These exercise the transform's mechanics through the real pipeline — the `reject` budget
policy (413 on an over-budget page), the §7.4 options schema (`prior_ledger` accepts an
object or null), and the ledger output schema (a missing required field drives the retry
path) — with a deterministic FakeLLM. They never assert model wording; real generation on
`qwen3.5:9b` and the sequential-threading run live in `test_gpu.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tts.concurrency import GenerationGate
from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.scene_update import build_scene_update

_FIXTURES = Path(__file__).parent / "fixtures" / "book"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _start_options() -> dict:
    return json.loads((_FIXTURES / "scene_start.json").read_text(encoding="utf-8"))


async def _run(fake: FakeLLMClient, text: str, options: dict) -> dict:
    return await run_transform(
        build_scene_update(), text, options, fake, GenerationGate(queue_wait_s=90.0)
    )


# A schema-valid ledger (the §7.4 worked micro-example shape).
_VALID_LEDGER = {
    "location": "the Time Traveller's smoking-room, Richmond",
    "time_of_day": "evening",
    "atmosphere": "lamplit, expectant",
    "present": ["the Time Traveller", "Filby", "the Psychologist", "the Medical Man"],
    "scene_changed": False,
    "visual_salience": 0.8,
    "best_visual_beat": "The tiny model machine blurs and vanishes from the table.",
    "carry_notes": "TT demonstrated a working model time machine that vanished from the table.",
}

_LEDGER_FIELDS = {
    "location",
    "time_of_day",
    "atmosphere",
    "present",
    "scene_changed",
    "visual_salience",
    "best_visual_beat",
    "carry_notes",
}


def test_transform_binding_and_shape():
    t = build_scene_update()
    assert t.name == "scene-update"
    assert t.model == "qwen3.5:9b"  # T3 rebind from §7.4's absent qwen3:8b
    assert t.input_budget == 1600
    assert t.over_budget == "reject"  # a page over budget is a paginator bug -> 413


async def test_prior_ledger_null_happy_path():
    # Page 1: prior_ledger is null (the scene_start.json payload). Valid options + valid
    # generation -> 200 with the full 8-field ledger.
    fake = FakeLLMClient([json.dumps(_VALID_LEDGER)])
    result = await _run(fake, _fixture("page_a.txt"), _start_options())
    assert set(result["output"]) == _LEDGER_FIELDS
    assert result["meta"]["transform"] == "scene-update"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["attempts"] == 1


async def test_prior_ledger_object_happy_path():
    # A later page: prior_ledger is the previous ledger object. Both forms pass the
    # options_schema `{"type": ["object", "null"]}`.
    options = {
        "prior_ledger": _VALID_LEDGER,
        "cast_names": ["the Time Traveller", "Filby"],
        "era": "late-Victorian England, 1890s",
    }
    fake = FakeLLMClient([json.dumps(_VALID_LEDGER)])
    result = await _run(fake, _fixture("page_b.txt"), options)
    assert set(result["output"]) == _LEDGER_FIELDS
    assert "warnings" not in result["meta"]  # scene-update has no soft validators


async def test_over_budget_page_is_413_and_never_calls_llm():
    # scene-update rejects (does not truncate) an over-budget page: ~1300 words is
    # ceil(1300*1.35)=1755 est-tokens, over the 1600 budget -> 413 before any generation.
    fake = FakeLLMClient([json.dumps(_VALID_LEDGER)])
    with pytest.raises(TransformError) as exc:
        await _run(fake, "word " * 1300, _start_options())
    assert exc.value.status == 413
    assert exc.value.code == "over_budget"
    assert fake.calls == []


async def test_missing_required_ledger_field_drives_retry_then_422():
    # A ledger missing a required field (carry_notes) fails the output_schema; the pipeline
    # retries and, still invalid, returns 422 with one reason per attempt.
    incomplete = {k: v for k, v in _VALID_LEDGER.items() if k != "carry_notes"}
    fake = FakeLLMClient(lambda *a: json.dumps(incomplete))  # same invalid ledger every attempt
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("page_a.txt"), _start_options())
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert len(reasons) == build_scene_update().retries + 1
    assert all("schema" in r for r in reasons)
