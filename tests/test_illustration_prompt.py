"""Unit tests for the `illustration-prompt` transform (DESIGN §7.5), FakeLLM only.

These exercise the transform's mechanics through the real pipeline — the §7.5 options
schema (`cast` array shape), the hard validators (a caller-side medium word is drift ->
422), and the soft `depicted ⊆ cast` validator (a stray depicted name is recorded to
meta.warnings, never fatal) — with a deterministic FakeLLM. They never assert model
wording; real generation on `qwen3.5:9b` lives in `test_gpu.py`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.illustration_prompt import build_illustration_prompt

_FIXTURES = Path(__file__).parent / "fixtures" / "book"

_LEDGER = {
    "best_visual_beat": "The tiny model machine blurs and vanishes from the table.",
    "location": "the Time Traveller's smoking-room, Richmond",
    "time_of_day": "evening",
    "atmosphere": "lamplit, expectant",
}


def _cast() -> list:
    return json.loads((_FIXTURES / "illustration_cast.json").read_text(encoding="utf-8"))


def _options(**overrides) -> dict:
    base = {"ledger": _LEDGER, "cast": _cast(), "era": "late-Victorian England, 1890s"}
    base.update(overrides)
    return base


async def _run(fake: FakeLLMClient, options: dict, text: str = "A page of prose.") -> dict:
    return await run_transform(
        build_illustration_prompt(), text, options, fake, asyncio.Semaphore(1), 90.0
    )


# A schema- and hard-validator-valid prompt (37 words, no medium/style words), depicting
# only "the Time Traveller" (who is in the cast) — the clean happy path.
_VALID_OUTPUT = {
    "prompt": (
        "A small brass and ivory machine shimmering into transparency on an octagonal parlor "
        "table, four Victorian gentlemen in frock coats leaning in around it under a shaded "
        "lamp, pipe smoke drifting through a cluttered lamplit smoking room."
    ),
    "depicted": ["the Time Traveller"],
    "shot": "medium",
}


def test_transform_binding_and_shape():
    t = build_illustration_prompt()
    assert t.name == "illustration-prompt"
    assert t.model == "qwen3.5:9b"  # T3 rebind from §7.5's absent qwen3:8b
    assert t.input_budget == 1600
    assert t.over_budget == "reject"


async def test_cast_entry_missing_one_line_is_400_bad_options():
    # The §7.5 options_schema requires each cast entry to have name AND one_line.
    fake = FakeLLMClient([json.dumps(_VALID_OUTPUT)])
    with pytest.raises(TransformError) as exc:
        await _run(fake, _options(cast=[{"name": "the Time Traveller"}]))
    assert exc.value.status == 400
    assert exc.value.code == "bad_options"
    assert fake.calls == []


async def test_medium_word_is_422_validation_failed():
    # A caller-side medium word ("watercolor") in the prompt is drift -> hard validator -> 422.
    drift = {
        "prompt": (
            "A delicate watercolor of a small brass and ivory machine vanishing from an "
            "octagonal table while four Victorian gentlemen in frock coats lean in beneath a "
            "shaded lamp in a cluttered room."
        ),
        "depicted": ["the Time Traveller"],
        "shot": "medium",
    }
    fake = FakeLLMClient(lambda *a: json.dumps(drift))  # same drift on every retried attempt
    with pytest.raises(TransformError) as exc:
        await _run(fake, _options())
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    assert all("banned substring" in r for r in exc.value.detail["reasons"])


async def test_depicted_not_in_cast_is_soft_warning_with_200():
    # A depicted name outside the cast is recorded to meta.warnings — the request still
    # succeeds (200), never 422 (DESIGN §7.5 "warn not fail" posture on name sets).
    out = {**_VALID_OUTPUT, "depicted": ["the Morlock"]}
    fake = FakeLLMClient([json.dumps(out)])
    result = await _run(fake, _options())
    assert set(result["output"]) >= {"prompt", "depicted", "shot"}
    assert result["meta"]["attempts"] == 1
    warnings = result["meta"]["warnings"]
    assert len(warnings) == 1 and "the Morlock" in warnings[0]


async def test_happy_path_depicted_subset_no_warnings():
    fake = FakeLLMClient([json.dumps(_VALID_OUTPUT)])
    result = await _run(fake, _options())
    assert set(result["output"]) >= {"prompt", "depicted", "shot"}
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert "warnings" not in result["meta"]  # depicted ⊆ cast -> no soft finding
