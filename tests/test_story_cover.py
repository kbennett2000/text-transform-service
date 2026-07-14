"""Unit tests for the `story-cover` transform (Brickfeed request §1, T9), FakeLLM only.

These exercise the transform's *mechanics* through the real pipeline — budget/truncation, the
reconciled five-field output schema (incl. the `category` enum), and the subject-neutral
validators (`banned_substrings` + `word_range`, mirroring `image-prompt`) — with a deterministic
FakeLLM. They never assert model wording (that is the GPU test's human-eyeball job). Real
generation on `qwen3.5:9b` lives in `test_gpu.py`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.story_cover import build_story_cover

_FIXTURES = Path(__file__).parent / "fixtures" / "story_cover"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


async def _run(fake: FakeLLMClient, text: str) -> dict:
    """Run the real story-cover transform through the pipeline with a fresh semaphore."""
    return await run_transform(
        build_story_cover(), text, {}, fake, asyncio.Semaphore(1), 90.0
    )


# A valid five-field cover bundle: passes the schema (bounds + enum) and every validator
# (imagePrompt: no banned substrings, 8-60 words; headline/caption/description clean). Subject
# only — no style/medium words — but the validators enforce shape, not wording.
_VALID = {
    "headline": "Downtown gains a connected grid of protected bike lanes",
    "description": (
        "The city council voted to build a network of protected bike lanes across the "
        "downtown core, linking existing routes and giving cyclists a continuous path. "
        "Construction is expected to begin next year."
    ),
    "imagePrompt": (
        "A crowd of cyclists in bright helmets rides down a sunlit street lined with "
        "fresh green painted lanes and small potted trees"
    ),
    "category": "BUSINESS",
    "caption": "Cyclists stream down a freshly painted downtown avenue at midday",
}
_OUTPUT_KEYS = {"headline", "description", "imagePrompt", "category", "caption"}


def test_transform_binding_and_shape():
    t = build_story_cover()
    assert t.name == "story-cover"
    assert t.version == "0.1.0"
    assert t.model == "qwen3.5:9b"
    assert t.input_budget == 1200
    assert t.over_budget == "truncate"
    assert t.truncation_strategy == "head"
    assert t.options_schema == {}


async def test_short_fixture_happy_path_not_truncated():
    fake = FakeLLMClient([json.dumps(_VALID)])
    result = await _run(fake, _fixture("01_bike_lanes.txt"))
    assert set(result["output"]) == _OUTPUT_KEYS
    assert result["output"]["category"] == "BUSINESS"
    assert result["meta"]["transform"] == "story-cover"
    assert result["meta"]["transform_version"] == "0.1.0"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["truncated"] is False
    assert result["meta"]["attempts"] == 1


async def test_over_budget_single_paragraph_is_noop_not_truncated():
    # The story-cover input is a single paragraph (no blank line), and `head` truncation only
    # cuts on paragraph boundaries (budget.py). So an input well over the 1200-token budget
    # passes through unchanged: truncated stays False and nothing is rejected. This documents
    # the reconciled no-op behavior (see the module docstring / NOTES).
    over_budget = "Source article title: " + "word " * 1000  # ~1002 words, one paragraph
    fake = FakeLLMClient([json.dumps(_VALID)])
    result = await _run(fake, over_budget)
    assert result["meta"]["input_tokens_est"] > 1200
    assert result["meta"]["truncated"] is False
    assert set(result["output"]) == _OUTPUT_KEYS


async def test_banned_substring_in_imageprompt_is_422_validation_failed():
    # Markdown/URL leakage in imagePrompt must be caught after generation; every attempt fails.
    polluted = dict(_VALID)
    polluted["imagePrompt"] = "A dramatic **bold** waterfront scene at http dawn with boats"
    fake = FakeLLMClient(lambda *a: json.dumps(polluted))  # returned on every (retried) attempt
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


async def test_headline_below_min_length_is_422_schema_reject():
    # headline shorter than minLength 10 -> output_schema re-validation rejects it (422).
    bad = dict(_VALID)
    bad["headline"] = "too short"  # 9 chars
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_bike_lanes.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"


async def test_out_of_enum_category_is_422_schema_reject():
    # A category outside the fixed 8-value enum -> schema re-validation rejects it (422). In
    # production, constrained decoding makes this structurally impossible; FakeLLM bypasses the
    # grammar, so this proves the post-generation enum guard.
    bad = dict(_VALID)
    bad["category"] = "GOSSIP"
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_bike_lanes.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
