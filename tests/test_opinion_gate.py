"""Unit tests for the `opinion-gate` transform (Brickfeed request §2, T10), FakeLLM only.

These exercise the transform's *mechanics* through the real pipeline — the `over_budget=reject`
413 path, the reconciled verdict schema (incl. the three-value enum with `uncertain`), the
bounded `reason`, and the nested `no_empty_strings` validators — with a deterministic FakeLLM.
They never assert model wording or a model's actual verdict (that is the GPU test's
human-eyeball job). Real generation on `qwen3.5:9b` lives in `test_gpu.py`.

Charter note: opinion-gate is a safety-relevant classifier admitted under ADR-0007. The service
stays fail-loud (these tests prove errors raise, never silently default a verdict); the caller's
fail-closed obligation lives in the module docstring + the RESPONSE doc, not in TTS.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.llm import FakeLLMClient
from tts.pipeline import TransformError, run_transform
from tts.transforms.opinion_gate import build_opinion_gate

_FIXTURES = Path(__file__).parent / "fixtures" / "opinion_gate"
_VERDICTS = {"eligible", "excluded", "uncertain"}


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


async def _run(fake: FakeLLMClient, text: str) -> dict:
    """Run the real opinion-gate transform through the pipeline with a fresh semaphore."""
    return await run_transform(
        build_opinion_gate(), text, {}, fake, asyncio.Semaphore(1), 90.0
    )


# A valid verdicts payload for the two-story 01_mixed fixture: a mix of verdicts, each with a
# non-empty reason within bounds. Shape only — the FakeLLM bypasses the model's real judgment.
_VALID = {
    "verdicts": [
        {"id": "a1", "verdict": "eligible", "reason": "Lighthearted fair story, no harm."},
        {"id": "b2", "verdict": "excluded", "reason": "Centers deaths in a crash."},
    ]
}


def test_transform_binding_and_shape():
    t = build_opinion_gate()
    assert t.name == "opinion-gate"
    assert t.version == "0.3.0"  # T12: num_ctx fix for large-batch output truncation
    assert t.model == "qwen3.5:9b"
    assert t.input_budget == 8000  # T11: raised from 1600 for real batch volumes
    assert t.num_predict == 5120  # T11: output ceiling for a verdict-per-candidate batch
    # T12: computed context window = input_budget + num_predict + 1024 headroom. Ollama's 4096
    # default starved generation at ~34-candidate volume (prompt filled the window) → 422.
    assert t.num_ctx == 8000 + 5120 + 1024  # == 14144
    assert t.over_budget == "reject"  # T11: unchanged — never truncate a batch
    assert t.options_schema == {}
    # ADR-0007 condition 1: the verdict enum is closed and includes an explicit `uncertain`.
    verdict_enum = t.output_schema["properties"]["verdicts"]["items"]["properties"]["verdict"][
        "enum"
    ]
    assert set(verdict_enum) == _VERDICTS


async def test_happy_path_mixed_verdicts():
    fake = FakeLLMClient([json.dumps(_VALID)])
    result = await _run(fake, _fixture("01_mixed.txt"))
    verdicts = result["output"]["verdicts"]
    assert {v["id"] for v in verdicts} == {"a1", "b2"}
    assert all(v["verdict"] in _VERDICTS for v in verdicts)
    assert result["meta"]["transform"] == "opinion-gate"
    assert result["meta"]["transform_version"] == "0.3.0"
    assert result["meta"]["model"] == "qwen3.5:9b"
    assert result["meta"]["attempts"] == 1


async def test_uncertain_verdict_is_accepted():
    # The third enum value must pass schema re-validation — proving TTS emits `uncertain`
    # honestly rather than forcing a binary verdict (ADR-0007 deviation 1).
    payload = {
        "verdicts": [
            {"id": "a1", "verdict": "uncertain", "reason": "Cannot tell if satire would harm."},
            {"id": "b2", "verdict": "excluded", "reason": "Centers deaths in a crash."},
        ]
    }
    fake = FakeLLMClient([json.dumps(payload)])
    result = await _run(fake, _fixture("01_mixed.txt"))
    verdicts = {v["id"]: v["verdict"] for v in result["output"]["verdicts"]}
    assert verdicts["a1"] == "uncertain"


async def test_realistic_batch_passes_budget():
    # T11 regression: the 21-candidate batch shape that 413'd Brickfeed at input_budget=1600
    # (~2.5k est-tokens) must now pass under the raised 8000 budget and reach generation. We
    # feed a FakeLLM one valid verdict per input id (shape only) and assert the pipeline
    # returns all 21 with id-set equality — proving the batch fit the budget, not the verdicts.
    text = _fixture("06_realistic_batch.txt")
    ids = [s["id"] for s in json.loads(text)]
    assert len(ids) == 21
    payload = {
        "verdicts": [
            {"id": sid, "verdict": "eligible", "reason": "Shape-only fake verdict."}
            for sid in ids
        ]
    }
    fake = FakeLLMClient([json.dumps(payload)])
    result = await _run(fake, text)
    verdicts = result["output"]["verdicts"]
    assert {v["id"] for v in verdicts} == set(ids)
    assert len(verdicts) == len(ids)  # no missing/duplicated ids at volume
    assert fake.calls, "budget passed -> the LLM should have been called"


async def test_over_budget_is_413_before_any_llm_call():
    # over_budget="reject": an input above the 8000-token budget (T11) must 413 *before*
    # generation. 100 padded candidates (~11.6k est-tokens) clears the raised budget; the
    # LLM is never called.
    big = json.dumps(
        [{"id": f"s{i}", "title": "word " * 40, "summary": "word " * 40} for i in range(100)]
    )
    fake = FakeLLMClient([json.dumps(_VALID)])
    with pytest.raises(TransformError) as exc:
        await _run(fake, big)
    assert exc.value.status == 413
    assert exc.value.code == "over_budget"
    assert fake.calls == []  # fired before any LLM call


async def test_out_of_enum_verdict_is_422_schema_reject():
    # A verdict outside the fixed three-value enum -> schema re-validation rejects it (422). In
    # production, constrained decoding makes this structurally impossible; FakeLLM bypasses the
    # grammar, so this proves the post-generation enum guard.
    bad = {
        "verdicts": [
            {"id": "a1", "verdict": "maybe", "reason": "Not sure."},
            {"id": "b2", "verdict": "excluded", "reason": "Centers deaths in a crash."},
        ]
    }
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_mixed.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"


async def test_reason_over_max_length_is_422_schema_reject():
    # reason longer than maxLength 200 -> schema re-validation rejects it (422).
    bad = {
        "verdicts": [
            {"id": "a1", "verdict": "eligible", "reason": "x" * 201},
            {"id": "b2", "verdict": "excluded", "reason": "Centers deaths in a crash."},
        ]
    }
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_mixed.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"


async def test_whitespace_only_reason_is_422_validation_failed():
    # A whitespace-only reason passes minLength:1 but trips no_empty_strings on verdicts[].reason.
    bad = {
        "verdicts": [
            {"id": "a1", "verdict": "eligible", "reason": "   "},
            {"id": "b2", "verdict": "excluded", "reason": "Centers deaths in a crash."},
        ]
    }
    fake = FakeLLMClient(lambda *a: json.dumps(bad))
    with pytest.raises(TransformError) as exc:
        await _run(fake, _fixture("01_mixed.txt"))
    assert exc.value.status == 422
    assert exc.value.code == "validation_failed"
    reasons = exc.value.detail["reasons"]
    assert reasons and any("empty string" in r for r in reasons)
