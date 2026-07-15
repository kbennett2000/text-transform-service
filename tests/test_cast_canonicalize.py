"""Unit tests for the `cast-canonicalize` transform (DESIGN §7.3), FakeLLM only.

These exercise the transform's mechanics through the real pipeline — options-schema
enforcement (evidence rides in `options`; `text` is empty), the §7.3 output schema, and
the `banned_substrings(visual_description, ...)` trait-drift guard — with a deterministic
FakeLLM. They never assert model wording; real generation on `qwen3.5:9b` lives in
`test_gpu.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tts.concurrency import GenerationGate
from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.cast_canonicalize import build_cast_canonicalize

_FIXTURES = Path(__file__).parent / "fixtures" / "book"


def _options() -> dict:
    return json.loads(
        (_FIXTURES / "canonicalize_time_traveller.json").read_text(encoding="utf-8")
    )


async def _run(fake: FakeLLMClient, options: dict) -> dict:
    """Run the real cast-canonicalize transform; text is empty (evidence is in options)."""
    return await run_transform(
        build_cast_canonicalize(), "", options, fake, GenerationGate(queue_wait_s=90.0)
    )


# A schema-valid canonicalize response drawn only from the evidence — no banned trait words.
_VALID_OUTPUT = {
    "visual_description": (
        "A pale, haggard man of middle age in dusty evening clothes, his disordered "
        "hair gone grey, a half-healed brown cut on his chin, moving with a limp."
    ),
    "one_line": "A limping, grey-haired Victorian gentleman in dusty evening dress.",
    "tags": ["grey hair", "pale face", "evening clothes", "limp"],
}


def test_transform_binding_and_shape():
    t = build_cast_canonicalize()
    assert t.name == "cast-canonicalize"
    assert t.model == "qwen3.5:9b"  # T3 rebind from §7.3's absent qwen3:8b
    assert t.input_budget == 1200


async def test_missing_descriptors_is_400_bad_options():
    # descriptors is required by the options_schema; its absence must 400 before any
    # generation (options are validated first).
    options = _options()
    del options["descriptors"]
    fake = FakeLLMClient([json.dumps(_VALID_OUTPUT)])
    with pytest.raises(TransformError) as exc:
        await _run(fake, options)
    assert exc.value.status == 400
    assert exc.value.code == "bad_options"
    assert fake.calls == []  # rejected before the LLM was ever called


async def test_personality_word_is_422_validation_failed():
    # The banned trait words ("brave"/"kind"/"personality") guard against drift out of
    # visual territory; a fake response containing "brave" fails every attempt -> 422.
    polluted = json.dumps(
        {
            "visual_description": (
                "A brave-looking pale man in dusty evening clothes with disordered grey "
                "hair and a half-healed cut on his chin, moving with a limp."
            ),
            "one_line": "A limping grey-haired gentleman.",
            "tags": ["grey hair", "limp"],
        }
    )
    fake = FakeLLMClient(lambda *a: polluted)
    with pytest.raises(TransformError) as exc:
        await _run(fake, _options())
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert reasons and all("banned substring" in r for r in reasons)


async def test_happy_path_returns_canonical_entry():
    fake = FakeLLMClient([json.dumps(_VALID_OUTPUT)])
    result = await _run(fake, _options())
    assert set(result["output"]) == {"visual_description", "one_line", "tags"}
    assert result["meta"]["transform"] == "cast-canonicalize"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["attempts"] == 1
