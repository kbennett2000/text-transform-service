"""Unit tests for the `image-prompt` transform (DESIGN §7.1), FakeLLM only.

These exercise the transform's *mechanics* through the real pipeline — budget/truncation,
the §7.1 output schema, and the two validators (`banned_substrings`, `word_range`) — with a
deterministic FakeLLM. They never assert model wording (that is the GPU test's human-eyeball
job). Real generation on `qwen3.5:9b` lives in `test_gpu.py`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.image_prompt import build_image_prompt

_FIXTURES = Path(__file__).parent / "fixtures" / "news"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


async def _run(fake: FakeLLMClient, text: str) -> dict:
    """Run the real image-prompt transform through the pipeline with a fresh semaphore."""
    return await run_transform(
        build_image_prompt(), text, {}, fake, asyncio.Semaphore(1), 90.0
    )


# A valid one-line subject prompt: >=30 chars, 8-60 words, no banned substrings.
_VALID_PROMPT = (
    "A rescue worker in an orange skiff glides between flooded rooftops at dawn, "
    "carrying an elderly woman wrapped in a blanket across the brown water."
)


def test_transform_binding_and_shape():
    t = build_image_prompt()
    assert t.name == "image-prompt"
    assert t.model == "qwen3.5:9b"  # T3 rebind from §7.1's absent qwen3:8b
    assert t.input_budget == 3000
    assert t.truncation_strategy == "lede_first_n"
    assert t.over_budget == "truncate"


async def test_short_fixture_happy_path_not_truncated():
    fake = FakeLLMClient([json.dumps({"prompt": _VALID_PROMPT})])
    result = await _run(fake, _fixture("01_quake.txt"))
    assert set(result["output"]) == {"prompt"}
    assert result["meta"]["transform"] == "image-prompt"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["truncated"] is False
    assert result["meta"]["attempts"] == 1


async def test_long_fixture_triggers_lede_first_n_truncation():
    # 05_flood_long is >3000 est-tokens; lede_first_n keeps the lede + following paras to
    # budget, flags meta.truncated, and the post-truncation estimate is within budget.
    fake = FakeLLMClient([json.dumps({"prompt": _VALID_PROMPT})])
    result = await _run(fake, _fixture("05_flood_long.txt"))
    assert result["meta"]["truncated"] is True
    assert result["meta"]["input_tokens_est"] <= 3000


async def test_markdown_polluted_response_is_422_validation_failed():
    # Banned substrings (** and http) must be caught after generation; every attempt fails.
    polluted = json.dumps(
        {"prompt": "A dramatic **bold** waterfront scene at http dawn with several boats"}
    )
    fake = FakeLLMClient(lambda *a: polluted)  # returned on every (retried) attempt
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_quake.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert reasons and all("banned substring" in r for r in reasons)


async def test_word_range_rejects_too_few_words():
    # >=30 chars (passes schema minLength) but only 3 words -> word_range(8, 60) fails.
    too_few = json.dumps(
        {"prompt": "supercalifragilistic expialidocious antidisestablishmentarianism"}
    )
    fake = FakeLLMClient(lambda *a: too_few)
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_quake.txt"))
    assert exc.value.status == 422
    assert all("words outside range" in r for r in exc.value.detail["reasons"])


async def test_word_range_rejects_too_many_words():
    # 61 short words (<400 chars, passes schema maxLength) -> word_range(8, 60) fails.
    too_many = json.dumps({"prompt": " ".join(["a"] * 61)})
    fake = FakeLLMClient(lambda *a: too_many)
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_quake.txt"))
    assert exc.value.status == 422
    assert all("words outside range" in r for r in exc.value.detail["reasons"])
