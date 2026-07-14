"""Unit tests for the `opinion-image-brief` transform (Brickfeed request §4, T10), FakeLLM only.

These exercise the transform's *mechanics* through the real pipeline — the two-field output
schema (imagePrompt/caption bounds) and the subject-neutral validators (banned_substrings +
word_range, reused from `story-cover`/`image-prompt`) — with a deterministic FakeLLM. They never
assert model wording (that is the GPU test's human-eyeball job). Real generation on `qwen3.5:9b`
lives in `test_gpu.py`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.opinion_image_brief import build_opinion_image_brief

_FIXTURES = Path(__file__).parent / "fixtures" / "opinion_image_brief"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


async def _run(fake: FakeLLMClient, text: str) -> dict:
    """Run the real opinion-image-brief transform through the pipeline with a fresh semaphore."""
    return await run_transform(
        build_opinion_image_brief(), text, {}, fake, asyncio.Semaphore(1), 90.0
    )


# A valid subject-only brief: passes the schema bounds and every validator (imagePrompt: no
# banned substrings, 8-60 words; caption clean, >=15 chars). Subject only — no style words.
_VALID = {
    "imagePrompt": (
        "A gaggle of cyclists in bright helmets rides down a freshly painted green lane while a "
        "flustered pedestrian clutches a briefcase on the curb"
    ),
    "caption": "Cyclists rule a freshly painted lane as a pedestrian frets on the curb",
}
_OUTPUT_KEYS = {"imagePrompt", "caption"}


def test_transform_binding_and_shape():
    t = build_opinion_image_brief()
    assert t.name == "opinion-image-brief"
    assert t.version == "0.1.0"
    assert t.model == "qwen3.5:9b"
    assert t.input_budget == 3000
    assert t.over_budget == "truncate"
    assert t.truncation_strategy == "head"
    assert t.options_schema == {}


async def test_happy_path_two_fields():
    fake = FakeLLMClient([json.dumps(_VALID)])
    result = await _run(fake, _fixture("01_bike_lanes.txt"))
    assert set(result["output"]) == _OUTPUT_KEYS
    assert result["meta"]["transform"] == "opinion-image-brief"
    assert result["meta"]["transform_version"] == "0.1.0"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["attempts"] == 1


async def test_banned_substring_in_imageprompt_is_422_validation_failed():
    # Markdown/URL leakage in imagePrompt must be caught after generation; every attempt fails.
    polluted = dict(_VALID)
    polluted["imagePrompt"] = "A **bold** street scene at http dawn with cyclists and a pedestrian"
    fake = FakeLLMClient(lambda *a: json.dumps(polluted))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_bike_lanes.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert reasons and all("banned substring" in r for r in reasons)


async def test_imageprompt_too_many_words_is_422():
    # 61 short words: passes schema length bounds but trips word_range(8, 60).
    too_many = dict(_VALID)
    too_many["imagePrompt"] = " ".join(["scene"] * 61)
    fake = FakeLLMClient(lambda *a: json.dumps(too_many))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_bike_lanes.txt"))
    assert exc.value.status == 422
    assert all("words outside range" in r for r in exc.value.detail["reasons"])


async def test_caption_below_min_length_is_422_schema_reject():
    # caption shorter than minLength 15 -> output_schema re-validation rejects it (422).
    bad = dict(_VALID)
    bad["caption"] = "too short"  # 9 chars
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_bike_lanes.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
