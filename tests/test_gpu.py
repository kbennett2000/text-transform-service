"""GPU integration tests (DESIGN §10) — run only on the 5070 with Ollama up.

    make test-gpu        # uv run pytest -m gpu

These hit real Ollama and assert **schema conformance and pipeline mechanics only** — never
model wording (qwen3.5:2b quality is irrelevant here; it only proves the plumbing). The test
model is `qwen3.5:2b` (rebound from the absent `qwen3:0.6b`; see docs/models.md).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tts.config import Settings
from tts.llm import OllamaClient
from tts.pipeline import run_transform
from tts.transforms.echo import build_echo

pytestmark = pytest.mark.gpu

TEST_MODEL = "qwen3.5:2b"


@pytest.fixture
def client() -> OllamaClient:
    s = Settings.from_env()
    return OllamaClient(base_url=s.ollama_url, keep_alive=s.ollama_keep_alive)


async def test_echo_transform_returns_schema_valid_output(client):
    # Full pipeline against a real model: echo is bound to qwen3.5:2b.
    t = build_echo()
    assert t.model == TEST_MODEL
    result = await run_transform(
        t, "Hello world. Second sentence.", {}, client, asyncio.Semaphore(1), 90.0
    )
    assert set(result["output"]) == {"echo"}
    assert isinstance(result["output"]["echo"], str)
    assert result["output"]["echo"]  # non-empty
    assert result["meta"]["model"] == TEST_MODEL
    assert result["meta"]["attempts"] >= 1
    assert result["meta"]["latency_ms"] >= 0


async def test_constrained_decoding_forces_schema_even_without_json_prompt(client):
    # The ADR-0002 guarantee: `format` grammar forces schema-valid JSON even when the prompt
    # never mentions JSON. This is why the client uses /api/generate (see docs/models.md).
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence"],
        "properties": {"sentence": {"type": "string"}},
    }
    params = {
        "model": TEST_MODEL,
        "temperature": 0.0,
        "top_p": 0.8,
        "num_predict": 80,
        "think": False,
    }
    raw = await client.chat(
        [{"role": "user", "content": "Write one short sentence about the sea."}], schema, params
    )
    obj = json.loads(raw)  # must parse despite a non-JSON prompt
    assert set(obj) == {"sentence"}
    assert isinstance(obj["sentence"], str)


async def test_unload_empties_ps(client):
    # Load the model with a tiny generation, then unload and confirm /api/ps no longer lists it.
    params = {
        "model": TEST_MODEL,
        "temperature": 0.0,
        "top_p": 0.8,
        "num_predict": 8,
        "think": False,
    }
    await client.chat([{"role": "user", "content": "hi"}], {}, params)
    loaded_before = await client.list_loaded()
    assert any(TEST_MODEL in m for m in loaded_before), (
        f"expected {TEST_MODEL} loaded, got {loaded_before}"
    )

    await client.unload(TEST_MODEL)
    await asyncio.sleep(0.5)
    loaded_after = await client.list_loaded()
    assert all(TEST_MODEL not in m for m in loaded_after), f"still loaded: {loaded_after}"
