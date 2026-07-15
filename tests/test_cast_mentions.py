"""Unit tests for the `cast-mentions` transform (DESIGN §7.2), FakeLLM only.

These exercise the transform's mechanics through the real pipeline — the `reject` budget
policy (413 on an over-budget page), the §7.2 mentions schema, and the nested
`no_empty_strings(mentions[].name)` validator — with a deterministic FakeLLM. They never
assert model wording; real generation on `qwen3.5:9b` lives in `test_gpu.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tts.concurrency import GenerationGate
from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.cast_mentions import build_cast_mentions

_FIXTURES = Path(__file__).parent / "fixtures" / "book"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


async def _run(fake: FakeLLMClient, text: str) -> dict:
    """Run the real cast-mentions transform through the pipeline with a fresh semaphore."""
    return await run_transform(
        build_cast_mentions(), text, {}, fake, GenerationGate(queue_wait_s=90.0)
    )


# A schema-valid mentions response for a book page.
_VALID_MENTIONS = {
    "mentions": [
        {
            "name": "the Time Traveller",
            "aliases": ["our friend"],
            "descriptors": ["his face was ghastly pale", "haggard and drawn"],
            "is_person": True,
        },
        {
            "name": "the Editor",
            "aliases": [],
            "descriptors": [],
            "is_person": True,
        },
    ]
}


def test_transform_binding_and_shape():
    t = build_cast_mentions()
    assert t.name == "cast-mentions"
    assert t.model == "qwen3.5:9b"  # T3 rebind from §7.2's absent qwen3:8b
    assert t.input_budget == 1600
    assert t.over_budget == "reject"  # a page over budget is a paginator bug -> 413


async def test_over_budget_page_is_413_and_never_calls_llm():
    # cast-mentions rejects (does not truncate) an over-budget page: ~1300 words is
    # ceil(1300*1.35)=1755 est-tokens, over the 1600 budget. The pipeline must 413 before
    # any generation happens.
    fake = FakeLLMClient([json.dumps(_VALID_MENTIONS)])
    over_budget_text = "word " * 1300
    with pytest.raises(TransformError) as exc:
        await _run(fake, over_budget_text)
    assert exc.value.status == 413
    assert exc.value.code == "over_budget"
    assert fake.calls == []  # rejected before the LLM was ever called


async def test_nested_validator_catches_whitespace_name():
    # A whitespace-only name passes the schema's minLength:1 but must be caught by
    # no_empty_strings("mentions[].name") -> 422 after retries.
    bad = json.dumps(
        {"mentions": [{"name": " ", "aliases": [], "descriptors": [], "is_person": True}]}
    )
    fake = FakeLLMClient(lambda *a: bad)  # returned on every (retried) attempt
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_dialogue.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert reasons and all("mentions[0].name" in r for r in reasons)


async def test_happy_path_returns_mentions():
    fake = FakeLLMClient([json.dumps(_VALID_MENTIONS)])
    result = await _run(fake, _fixture("01_dialogue.txt"))
    assert set(result["output"]) == {"mentions"}
    assert result["meta"]["transform"] == "cast-mentions"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["truncated"] is False
    assert result["meta"]["attempts"] == 1


async def test_empty_mentions_list_is_valid():
    # A zero-character page returns mentions: [] — schema-valid, no validator complaint.
    fake = FakeLLMClient([json.dumps({"mentions": []})])
    result = await _run(fake, _fixture("02_description.txt"))
    assert result["output"] == {"mentions": []}
